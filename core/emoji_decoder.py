"""自定义 Emoji 解码器：Telegram Premium Custom Emoji → ASCII 字符。

核心洞察：KKPay 等会生成无限多的 emoji 包（doc_id / set_id 永不重复），
但相同字符的缩略图字节完全一致。因此以「缩略图 MD5」为稳定缓存键：
新 doc_id 但图片相同 → 哈希命中 → 秒级返回，无需 AI。

解码流程（按 thumb_hash 去重）：
1. 下载所有缩略图（并行，~2KB/个）→ 算 MD5
2. 按 hash 批量查 Redis/PG → 命中直接用
3. 未命中的 hash → AI 识别（并行）→ 存 Redis + PG（键=hash）

缓存键：
- emoji:hash:{md5}     → 字符（稳定，跨包共享）
- emoji:doc:{doc_id}   → 字符（同消息重复检测的快路径）
- emoji:dochash:{doc_id} → md5（纠错时反查 hash）
"""
import asyncio
import base64
import hashlib
import logging

import httpx
from telethon import TelegramClient
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
from telethon.tl.types import MessageEntityCustomEmoji

from . import cache
from .config import config as app_config
from .notifier import broadcast_channel

log = logging.getLogger("core.emoji_decoder")


async def _ai_identify(thumb_bytes: bytes, want_digit: bool | None = None,
                       vision_config: dict | None = None) -> str:
    """用视觉 AI 识别缩略图上的字母/数字。
    want_digit: True=只可能是数字 / False=只可能是字母 / None=未知。
    传入类型可大幅提升识别准确率（约束输出空间，避免 1↔4、0↔Ø 等混淆）。
    vision_config: 可选的模块级视觉 API 配置覆盖。"""
    vc = vision_config or {}
    api_key = vc.get("vision_api_key") or app_config.vision_api_key
    base_url = vc.get("vision_base_url") or app_config.vision_base_url
    model = vc.get("vision_model") or app_config.vision_model
    if not thumb_bytes or not api_key:
        return ""
    if want_digit is True:
        prompt = "图片上是一个阿拉伯数字（0到9之间）。只回答这一个数字，不要任何其他内容。"
    elif want_digit is False:
        prompt = "图片上是一个英文大写字母（A到Z之间）。只回答这一个字母，不要任何其他内容。"
    else:
        prompt = "图片上是什么字母或数字？只回答一个字符。"
    b64 = base64.b64encode(thumb_bytes).decode()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "max_tokens": 10,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                },
            )
            if resp.status_code == 200:
                answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
                # 按类型过滤：数字只留 0-9，字母只留 A-Z
                if want_digit is True:
                    ch = "".join(c for c in answer if c.isdigit())
                elif want_digit is False:
                    ch = "".join(c for c in answer if c.isascii() and c.isalpha())
                else:
                    ch = "".join(c for c in answer if c.isascii() and c.isalnum())
                if ch and len(ch) <= 2:
                    return ch
    except Exception as e:
        log.debug(f"AI 识别失败: {e}")
    return ""


async def _download_thumb(client: TelegramClient, doc) -> tuple[int, str | None, bytes | None]:
    """下载单个缩略图并算 MD5，返回 (doc_id, hash, bytes)。"""
    if not doc.thumbs:
        return doc.id, None, None
    try:
        thumb = await client.download_media(doc, file=bytes, thumb=-1)
        if thumb:
            return doc.id, hashlib.md5(thumb).hexdigest(), thumb
    except Exception as e:
        log.debug(f"下载缩略图 {doc.id} 失败: {e}")
    return doc.id, None, None


# ── 缓存读写（按 hash） ──

async def _load_by_hash(hashes: list[str]) -> dict[str, str]:
    """Redis MGET + PG 并发查 hash → 字符。"""
    if not hashes:
        return {}
    result: dict[str, str] = {}
    hash_keys = [f"emoji:hash:{h}" for h in hashes]
    vals = await cache.mget(hash_keys)
    miss = []
    for h, v in zip(hashes, vals):
        if v:
            result[h] = v
        else:
            miss.append(h)
    if not miss:
        return result
    # PG 兜底
    from backend.db import SessionLocal
    from backend.models import EmojiMapping
    from sqlalchemy import select

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(EmojiMapping).where(EmojiMapping.thumb_hash.in_(miss))
        )).scalars().all()
        for r in rows:
            result[r.thumb_hash] = r.character
            await cache.set(f"emoji:hash:{r.thumb_hash}", r.character)
    return result


async def _save_by_hash(thumb_hash: str, ch: str, set_id: int = 0,
                        set_name: str = "", doc_id: int = 0):
    """按 hash 写 Redis + PG（并发安全 upsert）。"""
    await cache.set(f"emoji:hash:{thumb_hash}", ch)
    from backend.db import SessionLocal
    from backend.models import EmojiMapping
    from sqlalchemy import select

    try:
        async with SessionLocal() as s:
            row = (await s.execute(
                select(EmojiMapping).where(EmojiMapping.thumb_hash == thumb_hash)
            )).scalar_one_or_none()
            if row:
                row.character = ch
            else:
                s.add(EmojiMapping(
                    thumb_hash=thumb_hash, set_id=set_id, set_name=set_name,
                    doc_id=doc_id, character=ch))
            await s.commit()
    except Exception:
        pass


async def decode_custom_emoji(client: TelegramClient, doc_ids: list[int],
                              want_digit: bool | None = None,
                              vision_config: dict | None = None) -> dict[int, str]:
    """批量解码 document_id → ASCII 字符（按缩略图 hash 去重，破解无限包）。
    want_digit: 由按钮类型推断的期望类型，传给 AI 提升准确率。
    vision_config: 可选的模块级视觉 API 配置覆盖。"""
    if not doc_ids:
        return {}

    result: dict[int, str] = {}
    missing: list[int] = []

    # 1) doc 快路径 MGET（一次 Redis 往返取全部 doc_id 缓存）
    doc_keys = [f"emoji:doc:{d}" for d in doc_ids]
    cached_vals = await cache.mget(doc_keys)
    for did, val in zip(doc_ids, cached_vals):
        if val:
            result[did] = val
        else:
            missing.append(did)
    if not missing:
        return result

    # 2) 下载缩略图算 hash（并行）
    try:
        docs = await client(GetCustomEmojiDocumentsRequest(document_id=missing))
    except Exception as e:
        log.warning(f"获取 Custom Emoji 文档失败: {e}")
        return result
    thumbs = await asyncio.gather(*[_download_thumb(client, d) for d in docs])
    doc_hash: dict[int, str] = {}
    hash_bytes: dict[str, bytes] = {}
    dochash_kv: dict[str, str] = {}
    for did, h, b in thumbs:
        if h:
            doc_hash[did] = h
            hash_bytes[h] = b
            dochash_kv[f"emoji:dochash:{did}"] = h
    if dochash_kv:
        await cache.mset(dochash_kv)

    # 3) 按 hash 批量查缓存
    hash_map = await _load_by_hash(list(hash_bytes.keys()))

    # 4) 未命中的 hash → 先 SETNX 抢占，只有抢到的才 AI 识别（防多账号重复识别）
    ai_hashes = []
    for h in hash_bytes:
        if h in hash_map:
            continue
        locked = await cache.setnx(f"emoji:lock:{h}", "1", ex=120)
        if locked:
            ai_hashes.append(h)
        else:
            # 其他账号正在识别：高频轮询尽快拿到共享结果（总等待窗口 ~6s 不变）
            for _ in range(120):
                await asyncio.sleep(0.05)
                val = await cache.get(f"emoji:hash:{h}")
                if val:
                    hash_map[h] = val
                    break

    if ai_hashes:
        ai_results = await asyncio.gather(*[_ai_identify(hash_bytes[h], want_digit, vision_config) for h in ai_hashes])
        new_count = 0
        for h, ch in zip(ai_hashes, ai_results):
            if ch:
                hash_map[h] = ch
                await _save_by_hash(h, ch)
                new_count += 1
        if new_count:
            log.info(f"新识别 {new_count} 个字体")
            await _broadcast_new_glyphs(new_count)

    # 5) 组装 doc_id → char，并批量回填 doc 快路径
    backfill: dict[str, str] = {}
    for did, h in doc_hash.items():
        ch = hash_map.get(h)
        if ch:
            result[did] = ch
            backfill[f"emoji:doc:{did}"] = ch
    if backfill:
        await cache.mset(backfill)

    return result


async def update_mapping(doc_id: int, correct_char: str):
    """纠错后更新映射：按 doc 反查 hash，更新 hash 级映射（覆盖所有同图 emoji）。"""
    thumb_hash = await cache.get(f"emoji:dochash:{doc_id}")
    if not thumb_hash:
        log.debug(f"纠错找不到 doc {doc_id} 的 hash，跳过")
        return
    old = await cache.get(f"emoji:hash:{thumb_hash}")
    if old == correct_char:
        return
    log.info(f"纠正字体映射: {old} → {correct_char} (MD5 {thumb_hash[:8]})")
    await _save_by_hash(thumb_hash, correct_char)
    await cache.set(f"emoji:doc:{doc_id}", correct_char)
    await _broadcast_correction(old, correct_char)


async def reidentify_docs(client: TelegramClient, doc_ids: list[int],
                          want_digit: bool | None = None,
                          vision_config: dict | None = None) -> dict[int, str]:
    """验证码答错后，强制重新 AI 识别指定 doc（按 hash 更新）。"""
    if not doc_ids:
        return {}
    try:
        docs = await client(GetCustomEmojiDocumentsRequest(document_id=doc_ids))
    except Exception as e:
        log.warning(f"重新识别获取文档失败: {e}")
        return {}
    result: dict[int, str] = {}
    for doc in docs:
        _, h, b = await _download_thumb(client, doc)
        if h and b:
            ch = await _ai_identify(b, want_digit, vision_config)
            if ch:
                result[doc.id] = ch
                await _save_by_hash(h, ch, doc_id=doc.id)
                await cache.set(f"emoji:doc:{doc.id}", ch)
                await cache.set(f"emoji:dochash:{doc.id}", h)
    return result


# ── 通信频道播报 ──

async def _count_glyphs() -> int:
    """系统已识别的字体数（distinct thumb_hash）。"""
    from backend.db import SessionLocal
    from backend.models import EmojiMapping
    from sqlalchemy import func, select

    try:
        async with SessionLocal() as s:
            return (await s.execute(
                select(func.count(func.distinct(EmojiMapping.thumb_hash)))
            )).scalar() or 0
    except Exception:
        return 0


async def _broadcast_new_glyphs(count: int):
    if not app_config.broadcast_channel:
        return
    total = await _count_glyphs()
    text = f"🆕 新识别 {count} 个字体\n系统已识别 {total} 个字体"
    try:
        await broadcast_channel(app_config.notify_bot_token, app_config.broadcast_channel, text)
    except Exception as e:
        log.debug(f"字体播报失败: {e}")


async def _broadcast_correction(old: str | None, new: str):
    if not app_config.broadcast_channel:
        return
    text = f"🔧 字体纠正：{old or '?'} → {new}（按 MD5 修正）"
    try:
        await broadcast_channel(app_config.notify_bot_token, app_config.broadcast_channel, text)
    except Exception as e:
        log.debug(f"纠正播报失败: {e}")


def extract_custom_emoji_ids(msg) -> list[tuple[int, int, int]]:
    """从消息 entities 中提取 CustomEmoji: [(offset, length, document_id), ...]"""
    entities = getattr(msg, "entities", None) or []
    return [
        (e.offset, e.length, e.document_id)
        for e in entities
        if isinstance(e, MessageEntityCustomEmoji)
    ]
