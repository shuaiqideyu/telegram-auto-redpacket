"""账号路由：列表 / 登录（分步 + QR + Session 批量导入）/ 启停 / 删除。

手机号登录（三步，Telethon 需保持同一 client 持有 phone_code_hash）：
  POST /login/start    {phone}            → 发送验证码
  POST /login/code     {phone, code}      → 提交验证码（可能要二步验证）
  POST /login/password {phone, password}  → 提交两步验证密码

QR 扫码登录：
  POST /login/qr/start                    → 返回 qr_url + bind_id
  POST /login/qr/poll     {bind_id}       → 长轮询扫码状态（≤10 s）
  POST /login/qr/password {bind_id, pwd}  → 提交两步验证密码

Session 批量导入：
  POST /import-sessions   {sessions:[str]} → 逐条验证并入库
"""

import asyncio
import base64
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from core import cache
from core.config import config
from core.crypto import encrypt_session
from core.notifier import Notifier

from ..db import SessionLocal, get_session
from ..models import Account
from ..runner import runner
from ..schemas import (
    AccountOut,
    ImportSessions,
    LoginCode,
    LoginPassword,
    LoginStart,
    ProxyUpdate,
    QRLoginPassword,
    QRLoginPoll,
)

log = logging.getLogger("backend.accounts")
router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# 进行中的手机号登录：phone -> {client, hash}
_pending: dict[str, dict] = {}
# 进行中的 QR 登录：bind_id -> {client, qr_login, task, done_event, …}
_pending_qr: dict[str, dict] = {}


# ──────────────────────── 共用工具 ────────────────────────


def _out(acc: Account) -> AccountOut:
    st = runner.stats(acc.id)
    return AccountOut(
        id=acc.id, phone=acc.phone, user_id=acc.user_id, name=acc.name,
        username=acc.username, enabled=acc.enabled, status=acc.status,
        monitor_enabled=getattr(acc, "monitor_enabled", True),
        claim_enabled=getattr(acc, "claim_enabled", True),
        running=runner.is_running(acc.id),
        connected=runner.is_connected(acc.id),
        has_session=bool(acc.session_string),
        groups_count=runner.groups_count(acc.id),
        proxy=getattr(acc, "proxy", None),
        avatar_url=f"/api/accounts/{acc.id}/avatar",
        uptime_s=runner.uptime(acc.id),
        detected=st.get("detected", 0),
        success=st.get("success", 0),
        failed=st.get("failed", 0),
        created_at=acc.created_at,
    )


async def _save_account(client: TelegramClient, s: AsyncSession) -> Account:
    """从已登录 client 提取用户信息，绑定通知 bot，加密 session 后存入/更新 DB。"""
    me = await client.get_me()
    session_string = client.session.save()
    phone = me.phone or f"user_{me.id}"

    # 自动给通知 bot 发 /start，让 bot 能私聊推送领取通知
    await _bind_notify_bot(client, me)

    acc = (await s.execute(
        select(Account).where(Account.phone == phone)
    )).scalar_one_or_none()
    if acc is None:
        acc = Account(phone=phone)
        s.add(acc)
    acc.user_id = me.id
    acc.name = me.first_name
    acc.username = me.username
    acc.session_string = encrypt_session(session_string)
    acc.status = "authorized"
    acc.enabled = True

    # 登录时顺带同步 Telegram 头像（client 在手，失败不阻塞）
    try:
        photo = await client.download_profile_photo("me", file=bytes, download_big=False)
        if photo:
            acc.avatar_b64 = "data:image/jpeg;base64," + base64.b64encode(photo).decode()
    except Exception as e:
        log.debug("下载账号头像失败（忽略）: %s", e)

    await s.commit()
    await s.refresh(acc)

    # 添加后自动启动监听
    try:
        await runner.start(acc)
    except Exception as e:
        log.warning("自动启动监听失败（不影响登录）: %s", e)

    return acc


async def _bind_notify_bot(client: TelegramClient, me):
    """账号自动给通知机器人发 /start，建立私聊会话（幂等，失败不阻塞登录）。"""
    notifier = Notifier()
    if not notifier.enabled:
        log.warning("未配置 NOTIFY_BOT_TOKEN，跳过通知机器人绑定")
        return
    try:
        ok = await notifier.bind(client, me)
        if ok:
            log.info("通知机器人绑定成功: %s (%s)", me.first_name, me.id)
    except Exception as e:
        log.warning("通知机器人绑定失败（不影响登录）: %s", e)
    finally:
        await notifier.close()


# ──────────────────────── 账号列表 ────────────────────────


@router.get("", response_model=list[AccountOut])
async def list_accounts(s: AsyncSession = Depends(get_session)):
    accs = (await s.execute(select(Account).order_by(Account.id))).scalars().all()
    return [_out(a) for a in accs]


# ──────────────────────── 账号头像 ────────────────────────
# 两层 cache-aside：Redis 热缓存 → DB avatar_b64 → telethon 下载（账号自身 client）

_AVATAR_TTL = 7 * 86400
_AVATAR_MISS_TTL = 3600
_AVATAR_MISS = "0"
_AVATAR_HEADERS = {"Cache-Control": "public, max-age=86400"}
_avatar_sem = asyncio.Semaphore(2)


def _avatar_resp(data: bytes) -> Response:
    return Response(content=data, media_type="image/jpeg", headers=_AVATAR_HEADERS)


async def _avatar_db_get(account_id: int) -> tuple[Account | None, bytes | None]:
    async with SessionLocal() as s:
        acc = await s.get(Account, account_id)
        if not acc:
            return None, None
        if acc.avatar_b64:
            try:
                _, _, b64 = acc.avatar_b64.partition(",")
                return acc, base64.b64decode(b64)
            except Exception:
                pass
        return acc, None


@router.get("/{account_id}/avatar")
async def get_account_avatar(account_id: int):
    """账号头像懒加载：Redis → DB → telethon 下载，逐层回填。"""
    key = f"avatar:acct:{account_id}"
    try:
        rv = await cache.get(key)
    except Exception:
        rv = None
    if rv and rv != _AVATAR_MISS:
        try:
            return _avatar_resp(base64.b64decode(rv))
        except Exception:
            pass
    if rv == _AVATAR_MISS:
        raise HTTPException(404, "头像不存在")

    acc, cached = await _avatar_db_get(account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    if cached:
        try:
            await cache.set(key, base64.b64encode(cached).decode(), ex=_AVATAR_TTL)
        except Exception:
            pass
        return _avatar_resp(cached)

    async with _avatar_sem:
        data = await runner.download_self_avatar(account_id, acc.user_id)

    if data:
        async with SessionLocal() as s:
            row = await s.get(Account, account_id)
            if row:
                row.avatar_b64 = "data:image/jpeg;base64," + base64.b64encode(data).decode()
                await s.commit()
        try:
            await cache.set(key, base64.b64encode(data).decode(), ex=_AVATAR_TTL)
        except Exception:
            pass
        return _avatar_resp(data)

    try:
        await cache.set(key, _AVATAR_MISS, ex=_AVATAR_MISS_TTL)
    except Exception:
        pass
    raise HTTPException(404, "头像不存在")


# ──────────────────────── 手机号登录（三步） ────────────────────────


@router.post("/login/start")
async def login_start(body: LoginStart):
    phone = body.phone.strip()
    if not config.api_id or not config.api_hash:
        raise HTTPException(400, "未配置 API_ID / API_HASH")
    # 清理同号旧连接
    old = _pending.pop(phone, None)
    if old:
        try:
            await old["client"].disconnect()
        except Exception:
            pass
    client = TelegramClient(StringSession(), config.api_id, config.api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception as e:
        await client.disconnect()
        raise HTTPException(400, f"发送验证码失败: {e}")
    _pending[phone] = {"client": client, "hash": sent.phone_code_hash}
    log.info("login/start: phone=%s hash=%s", phone, sent.phone_code_hash[:8])
    return {"phone": phone, "needs_code": True}


@router.post("/login/code")
async def login_code(body: LoginCode, s: AsyncSession = Depends(get_session)):
    phone = body.phone.strip()
    p = _pending.get(phone)
    if not p:
        log.warning("login/code: _pending 中无 phone=%s, keys=%s", phone, list(_pending.keys()))
        raise HTTPException(400, "请先获取验证码")
    client = p["client"]
    if not client.is_connected():
        log.info("login/code: client 已断开，重新连接...")
        await client.connect()
    log.info("login/code: phone=%s code=%s hash=%s", phone, body.code.strip(), p["hash"][:8])
    try:
        await client.sign_in(phone, body.code.strip(), phone_code_hash=p["hash"])
    except SessionPasswordNeededError:
        return {"needs_password": True}
    except Exception as e:
        log.error("login/code 失败: %s (type=%s)", e, type(e).__name__)
        raise HTTPException(400, f"验证码错误: {e}")
    return await _finish_phone(phone, p["client"], s)


@router.post("/login/password")
async def login_password(body: LoginPassword, s: AsyncSession = Depends(get_session)):
    phone = body.phone.strip()
    p = _pending.get(phone)
    if not p:
        raise HTTPException(400, "登录会话已过期，请重新开始")
    try:
        await p["client"].sign_in(password=body.password)
    except Exception as e:
        raise HTTPException(400, f"两步验证失败: {e}")
    return await _finish_phone(phone, p["client"], s)


async def _finish_phone(phone: str, client: TelegramClient, s: AsyncSession):
    acc = await _save_account(client, s)
    await client.disconnect()
    _pending.pop(phone, None)
    log.info("账号登录成功: %s (%s)", acc.name, phone)
    return {"done": True, "account": _out(acc).model_dump(mode="json")}


# ──────────────────────── QR 扫码登录 ────────────────────────


@router.post("/login/qr/start")
async def qr_start():
    if not config.api_id or not config.api_hash:
        raise HTTPException(400, "未配置 API_ID / API_HASH")
    client = TelegramClient(StringSession(), config.api_id, config.api_hash)
    await client.connect()
    try:
        qr_login = await client.qr_login()
    except Exception as e:
        await client.disconnect()
        raise HTTPException(400, f"QR 登录初始化失败: {e}")

    bind_id = uuid.uuid4().hex
    done_event = asyncio.Event()
    entry: dict = {
        "client": client, "qr_login": qr_login,
        "done_event": done_event,
        "result": False, "needs_password": False,
        "expired": False, "error": None,
    }

    async def _bg_wait():
        try:
            await qr_login.wait()
            entry["result"] = True
        except SessionPasswordNeededError:
            entry["needs_password"] = True
        except asyncio.TimeoutError:
            entry["expired"] = True
        except Exception as exc:
            entry["error"] = str(exc)
        finally:
            done_event.set()

    entry["task"] = asyncio.create_task(_bg_wait())
    _pending_qr[bind_id] = entry
    return {
        "bind_id": bind_id,
        "qr_url": qr_login.url,
        "expires_at": qr_login.expires.isoformat(),
    }


@router.post("/login/qr/poll")
async def qr_poll(body: QRLoginPoll, s: AsyncSession = Depends(get_session)):
    entry = _pending_qr.get(body.bind_id)
    if not entry:
        raise HTTPException(400, "QR 登录会话不存在或已过期")

    if not entry["done_event"].is_set():
        try:
            await asyncio.wait_for(entry["done_event"].wait(), timeout=10)
        except asyncio.TimeoutError:
            return {"pending": True}

    if entry.get("error"):
        _pending_qr.pop(body.bind_id, None)
        try:
            await entry["client"].disconnect()
        except Exception:
            pass
        raise HTTPException(400, f"QR 登录失败: {entry['error']}")

    if entry["needs_password"]:
        return {"needs_password": True, "bind_id": body.bind_id}

    if entry["result"]:
        return await _finish_qr(body.bind_id, entry, s)

    if entry["expired"]:
        _pending_qr.pop(body.bind_id, None)
        try:
            await entry["client"].disconnect()
        except Exception:
            pass
        return {"expired": True}

    return {"pending": True}


@router.post("/login/qr/password")
async def qr_password(body: QRLoginPassword, s: AsyncSession = Depends(get_session)):
    entry = _pending_qr.get(body.bind_id)
    if not entry:
        raise HTTPException(400, "QR 登录会话不存在或已过期")
    try:
        await entry["client"].sign_in(password=body.password)
    except Exception as e:
        raise HTTPException(400, f"两步验证失败: {e}")
    return await _finish_qr(body.bind_id, entry, s)


async def _finish_qr(bind_id: str, entry: dict, s: AsyncSession):
    acc = await _save_account(entry["client"], s)
    try:
        await entry["client"].disconnect()
    except Exception:
        pass
    _pending_qr.pop(bind_id, None)
    log.info("QR 登录成功: %s (%s)", acc.name, acc.phone)
    return {"done": True, "account": _out(acc).model_dump(mode="json")}


# ──────────────────────── Session 批量导入 ────────────────────────


@router.post("/import-sessions")
async def import_sessions(body: ImportSessions, s: AsyncSession = Depends(get_session)):
    if not config.api_id or not config.api_hash:
        raise HTTPException(400, "未配置 API_ID / API_HASH")

    imported: list[dict] = []
    failed: list[dict] = []

    for raw in body.sessions:
        raw = raw.strip()
        if not raw:
            continue
        client = None
        try:
            client = TelegramClient(
                StringSession(raw), config.api_id, config.api_hash)
            await client.connect()
            me = await client.get_me()
            if not me:
                raise ValueError("session 无效或已过期")
            acc = await _save_account(client, s)
            imported.append(_out(acc).model_dump(mode="json"))
            log.info("Session 导入成功: %s (%s)", acc.name, acc.phone)
        except Exception as e:
            failed.append({"session": raw[:20] + "…", "error": str(e)})
            log.warning("Session 导入失败: %s", e)
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    return {
        "imported": len(imported), "failed": len(failed),
        "accounts": imported, "errors": failed,
    }


# ──────────────────────── 账号操作 ────────────────────────


@router.post("/{account_id}/enable")
async def set_enabled(account_id: int, enabled: bool = True,
                      s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.enabled = enabled
    await s.commit()
    return {"ok": True, "enabled": acc.enabled}


@router.post("/{account_id}/start")
async def start_account(account_id: int, s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if not acc.session_string:
        raise HTTPException(400, "账号未登录")
    acc.enabled = True
    await s.commit()
    await runner.start(acc)
    return {"ok": True, "running": runner.is_running(account_id)}


@router.post("/{account_id}/stop")
async def stop_account(account_id: int, s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.enabled = False
    await s.commit()
    await runner.stop(account_id)
    return {"ok": True, "running": runner.is_running(account_id)}


@router.post("/{account_id}/monitor")
async def set_monitor(account_id: int, enabled: bool = True, s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.monitor_enabled = enabled
    await s.commit()
    runner.update_flags(account_id, monitor=enabled)
    return {"ok": True, "monitor_enabled": enabled}


@router.post("/{account_id}/claim")
async def set_claim(account_id: int, enabled: bool = True, s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.claim_enabled = enabled
    await s.commit()
    runner.update_flags(account_id, claim=enabled)
    return {"ok": True, "claim_enabled": enabled}


@router.post("/{account_id}/proxy")
async def set_proxy(account_id: int, body: ProxyUpdate, s: AsyncSession = Depends(get_session)):
    """设置/清除账号代理。代理变更需重连才生效——运行中账号会自动重启监听。"""
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    acc.proxy = (body.proxy or "").strip() or None
    await s.commit()
    restarted = False
    if runner.is_running(account_id):
        await runner.stop(account_id)
        acc = await s.get(Account, account_id)
        if acc and acc.session_string:
            acc.enabled = True
            await s.commit()
            await runner.start(acc)
            restarted = True
    return {"ok": True, "proxy": acc.proxy if acc else None, "restarted": restarted}


@router.delete("/{account_id}")
async def delete_account(account_id: int, s: AsyncSession = Depends(get_session)):
    acc = await s.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    await runner.stop(account_id)
    await s.delete(acc)
    await s.commit()
    return {"ok": True}
