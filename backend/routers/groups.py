"""秒包群组路由：汇总所有账号窗口（去重）+ 每群独立秒包开关。

群列表通过 runner 遍历所有运行中账号的 iter_dialogs 自动汇总；
每个群一个 enabled 开关（默认开启），关闭后该群红包被忽略。
"""
import asyncio
import base64
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import cache

from ..db import SessionLocal, get_session
from ..models import MonitoredGroup
from ..runner import runner
from ..schemas import GroupBatchToggle, GroupPin, GroupToggle

log = logging.getLogger("backend.groups")

router = APIRouter(prefix="/api/groups", tags=["groups"])

# 限制并发 telethon 头像下载（页面一次性懒加载几百个头像会打爆连接池/telethon）
_avatar_sem = asyncio.Semaphore(4)

# 头像强缓存：浏览器命中后翻页/轮询不再重复请求
_AVATAR_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400"}


def _source_ids(src: str | None) -> list[int]:
    if not src:
        return []
    out = []
    for x in src.split(","):
        x = x.strip()
        if x.isdigit():
            out.append(int(x))
    return out


def _row_to_dict(row: MonitoredGroup) -> dict:
    ids = _source_ids(row.source_accounts)
    # 一律走头像端点 URL（浏览器强缓存），不再把 base64 内联进列表 JSON——
    # 否则群多时列表响应可达数 MB，每次轮询都要重新解析+重渲染。
    avatar = f"/api/groups/avatar/{abs(row.chat_id)}"
    return {
        "id": row.id,
        "chat_id": row.chat_id,
        "title": row.title,
        "username": row.username,
        "members_count": row.members_count,
        "chat_type": getattr(row, "chat_type", None) or "group",
        "avatar_url": avatar,
        "enabled": row.enabled,
        "pinned": getattr(row, "pinned", False),
        "source_count": len(ids),
        "source_account_ids": ids,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ordered():
    """置顶 > 已开启秒包 > 群人数降序（无人数排最后）。"""
    return select(MonitoredGroup).order_by(
        MonitoredGroup.pinned.desc(),
        MonitoredGroup.enabled.desc(),
        MonitoredGroup.members_count.desc().nulls_last())


async def _hot_reload(s: AsyncSession):
    """读取当前关闭的群 ID 集合并热推送到运行中的 grabber。"""
    rows = (await s.execute(
        select(MonitoredGroup.chat_id).where(MonitoredGroup.enabled.is_(False)))).scalars().all()
    runner.hot_reload_disabled_groups({cid for cid in rows if cid})


# ── 列表 / 扫描 ──

@router.get("")
async def list_groups(s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(_ordered())).scalars().all()
    return [_row_to_dict(r) for r in rows]


@router.post("/scan")
async def scan_groups(s: AsyncSession = Depends(get_session)):
    """触发扫描：遍历所有运行中账号的对话列表，汇总去重后 upsert 到 DB。
    保留已有群的 enabled / pinned 状态，新群默认开启。"""
    groups = await runner.scan_all_groups()
    if not groups:
        # 没有运行中的账号，直接返回已存的列表
        rows = (await s.execute(_ordered())).scalars().all()
        if not rows:
            raise HTTPException(400, "没有运行中的账号，请先在「账号管理」启动至少一个账号")
        return [_row_to_dict(r) for r in rows]

    existing = {r.chat_id: r for r in (await s.execute(
        select(MonitoredGroup))).scalars().all()}

    for g in groups:
        cid = g["chat_id"]
        src = ",".join(str(a) for a in g.get("source_accounts", []))
        row = existing.get(cid)
        if row:
            row.title = g.get("title") or row.title
            row.username = g.get("username") or row.username
            row.members_count = g.get("members_count")
            row.chat_type = g.get("chat_type") or row.chat_type
            row.source_accounts = src
        else:
            s.add(MonitoredGroup(
                chat_id=cid,
                title=g.get("title"),
                username=g.get("username"),
                members_count=g.get("members_count"),
                chat_type=g.get("chat_type") or "group",
                enabled=True,
                source_accounts=src,
            ))
    await s.commit()

    rows = (await s.execute(_ordered())).scalars().all()
    return [_row_to_dict(r) for r in rows]


# ── 开关 ──

@router.put("/{group_id}/toggle")
async def toggle_group(group_id: int, body: GroupToggle, s: AsyncSession = Depends(get_session)):
    row = await s.get(MonitoredGroup, group_id)
    if not row:
        raise HTTPException(404, "群组不存在")
    row.enabled = body.enabled
    # 趁 row 刚加载、所有列都在内存里，先快照成 dict，避免 commit/hot_reload 之后
    # 再访问 ORM 属性触发跨 greenlet 的惰性刷新（高并发下会报 MissingGreenlet）
    result = _row_to_dict(row)
    await s.commit()
    await _hot_reload(s)
    return result


@router.put("/{group_id}/pin")
async def pin_group(group_id: int, body: GroupPin, s: AsyncSession = Depends(get_session)):
    row = await s.get(MonitoredGroup, group_id)
    if not row:
        raise HTTPException(404, "群组不存在")
    row.pinned = body.pinned
    result = _row_to_dict(row)
    await s.commit()
    return result


@router.post("/batch")
async def batch_toggle(body: GroupBatchToggle, s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(MonitoredGroup))).scalars().all()
    for r in rows:
        r.enabled = body.enabled
    await s.commit()
    await _hot_reload(s)
    return {"ok": True, "count": len(rows)}


@router.delete("/{group_id}")
async def remove_group(group_id: int, s: AsyncSession = Depends(get_session)):
    row = await s.get(MonitoredGroup, group_id)
    if not row:
        raise HTTPException(404, "群组不存在")
    cid = abs(row.chat_id) if row.chat_id else None
    await s.delete(row)
    await s.commit()
    if cid is not None:
        try:
            await cache.delete(f"avatar:{cid}")
        except Exception:
            pass
    await _hot_reload(s)
    return {"ok": True}


# ── 头像代理（Redis 热缓存 → DB 持久层 → telethon 下载） ──
#
# 三层 cache-aside：
#   1. Redis（本地，毫秒级）—— 避开每次都打远程新加坡 RDS（201 群首屏=数百次跨境往返）
#   2. PostgreSQL avatar_b64（持久层，跨重启不丢）
#   3. telethon 下载 / t.me 兜底（最慢，仅首次或缓存过期时）
# 命中后回填上层；查无头像写短期负缓存，避免反复下载。

_CID_VARIANTS = lambda cid: [cid, -cid, -1000000000000 - cid]  # noqa: E731

_AVATAR_TTL = 7 * 86400      # 头像热缓存 7 天（头像极少变）
_AVATAR_MISS_TTL = 3600      # 无头像负缓存 1 小时
_AVATAR_MISS = "0"           # 负缓存哨兵（非合法 base64，不会与真图冲突）


async def _avatar_redis_get(cid: int):
    """读 Redis：bytes=命中图片 / _AVATAR_MISS=已知无头像 / None=未缓存。"""
    try:
        v = await cache.get(f"avatar:{cid}")
    except Exception:
        return None
    if v is None or v == _AVATAR_MISS:
        return v
    try:
        return base64.b64decode(v)
    except Exception:
        return None


async def _avatar_redis_set(cid: int, data: bytes | None):
    """写 Redis：有图存 base64（长 TTL），无图存负缓存哨兵（短 TTL）。"""
    try:
        if data:
            await cache.set(f"avatar:{cid}", base64.b64encode(data).decode(), ex=_AVATAR_TTL)
        else:
            await cache.set(f"avatar:{cid}", _AVATAR_MISS, ex=_AVATAR_MISS_TTL)
    except Exception:
        pass


async def _avatar_from_cache(cid: int) -> tuple[int | None, str | None, bytes | None]:
    """短会话读 DB。返回 (chat_id, username, 已缓存图片bytes)。"""
    async with SessionLocal() as s:
        row = (await s.execute(
            select(MonitoredGroup).where(
                MonitoredGroup.chat_id.in_(_CID_VARIANTS(cid))))).scalar_one_or_none()
        if not row:
            return None, None, None
        if row.avatar_b64:
            try:
                _, _, b64 = row.avatar_b64.partition(",")
                return row.chat_id, row.username, base64.b64decode(b64)
            except Exception:
                pass
        return row.chat_id, row.username, None


async def _avatar_save(chat_id: int, data: bytes):
    """短会话写 DB（持久层）。"""
    async with SessionLocal() as s:
        row = (await s.execute(
            select(MonitoredGroup).where(MonitoredGroup.chat_id == chat_id))).scalar_one_or_none()
        if row:
            row.avatar_b64 = "data:image/jpeg;base64," + base64.b64encode(data).decode()
            await s.commit()


def _avatar_resp(data: bytes) -> Response:
    return Response(content=data, media_type="image/jpeg", headers=_AVATAR_CACHE_HEADERS)


@router.get("/avatar/{cid}")
async def get_avatar(cid: int):
    """头像懒加载：Redis 热缓存 → DB 持久层 → telethon 下载，逐层回填。"""
    # 1) Redis 热缓存（本地，避开远程 RDS）
    rv = await _avatar_redis_get(cid)
    if isinstance(rv, bytes):
        return _avatar_resp(rv)
    if rv == _AVATAR_MISS:
        raise HTTPException(404, "头像不存在")

    # 2) DB 持久层；命中则回填 Redis
    chat_id, username, cached = await _avatar_from_cache(cid)
    if chat_id is None:
        raise HTTPException(404, "群组不存在")
    if cached:
        await _avatar_redis_set(cid, cached)
        return _avatar_resp(cached)

    # 3) 限流下载（下载期间不持有任何 DB 连接）
    async with _avatar_sem:
        # 二次检查 Redis + DB（排队期间可能已被其他请求填好）
        rv2 = await _avatar_redis_get(cid)
        if isinstance(rv2, bytes):
            return _avatar_resp(rv2)
        _, _, cached2 = await _avatar_from_cache(cid)
        if cached2:
            await _avatar_redis_set(cid, cached2)
            return _avatar_resp(cached2)
        data = await runner.download_avatar(chat_id)

    if data:
        await _avatar_save(chat_id, data)       # 持久化 DB
        await _avatar_redis_set(cid, data)      # 回填 Redis
        return _avatar_resp(data)

    # 兜底：公开用户名走 t.me
    if username:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=8) as http:
                resp = await http.get(f"https://t.me/i/userpic/320/{username}.jpg")
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                    await _avatar_save(chat_id, resp.content)
                    await _avatar_redis_set(cid, resp.content)
                    return _avatar_resp(resp.content)
        except Exception:
            pass

    # 查无头像 → 写短期负缓存，避免每次轮询/翻页都重新下载
    await _avatar_redis_set(cid, None)

    raise HTTPException(404, "头像不存在")
