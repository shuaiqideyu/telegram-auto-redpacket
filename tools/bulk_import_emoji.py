"""批量导入 emoji 包映射：遍历所有已知包 → 下载缩略图 → MD5 去重 → AI 识别新的 → 存库。

用法: python -m tools.bulk_import_emoji
"""
import asyncio
import hashlib
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from core import cache
from core.config import config
from core.crypto import decrypt_session
from core.emoji_decoder import _ai_identify, _save_by_hash

# 已知的包名列表（从日志提取）
KNOWN_PACKS = []


async def _load_packs_from_log():
    """从日志文件中提取所有包名。"""
    import os, re
    terms_dir = os.path.expanduser("~/.cursor/projects/Users-04by-Desktop-okpay/terminals")
    packs = set()
    if not os.path.isdir(terms_dir):
        return packs
    for f in os.listdir(terms_dir):
        if not f.endswith(".txt"):
            continue
        try:
            content = open(os.path.join(terms_dir, f), errors="ignore").read()
            packs.update(re.findall(r"ok\d+_by_okstickerbot|kkpay[A-Za-z0-9]*", content))
        except Exception:
            pass
    return packs


async def _get_existing_hashes() -> set[str]:
    """从 PG 获取已有的 thumb_hash 集合。"""
    from backend.db import SessionLocal
    from backend.models import EmojiMapping
    from sqlalchemy import select

    async with SessionLocal() as s:
        rows = (await s.execute(select(EmojiMapping.thumb_hash))).scalars().all()
        return {h for h in rows if h}


async def main():
    from backend.db import SessionLocal
    from backend.models import Account
    from sqlalchemy import select

    async with SessionLocal() as s:
        acc = (await s.execute(select(Account).limit(1))).scalar_one_or_none()
    if not acc:
        print("没有可用账号")
        return

    client = TelegramClient(
        StringSession(decrypt_session(acc.session_string)),
        config.api_id, config.api_hash)
    await client.connect()

    packs = await _load_packs_from_log()
    if not packs:
        print("未找到任何包名")
        return

    existing = await _get_existing_hashes()
    print(f"已知 {len(packs)} 个包，已有 {len(existing)} 个 MD5 映射", flush=True)

    total_new = 0
    total_skip = 0
    failed_packs = []

    for i, name in enumerate(sorted(packs), 1):
        sys.stdout.write(f"\r[{i}/{len(packs)}] {name}...")
        sys.stdout.flush()
        try:
            result = await client(GetStickerSetRequest(
                stickerset=InputStickerSetShortName(name), hash=0))
        except Exception as e:
            failed_packs.append(name)
            continue

        # 下载缩略图并算 hash
        new_in_pack = []
        for doc in result.documents:
            if not doc.thumbs:
                continue
            try:
                thumb = await client.download_media(doc, file=bytes, thumb=-1)
                if not thumb:
                    continue
                h = hashlib.md5(thumb).hexdigest()
                if h in existing:
                    total_skip += 1
                    continue
                new_in_pack.append((h, thumb))
                existing.add(h)
            except Exception:
                continue

        if not new_in_pack:
            continue

        # 按钮类型：okpay 包通常是纯字母或纯数字
        # 先判断包内是否全数字/全字母（按包名猜，okpay 的 36 个 = A-Z + 0-9）
        # AI 识别新的 hash（并行，最多 10 个一批）
        batch_size = 10
        for j in range(0, len(new_in_pack), batch_size):
            batch = new_in_pack[j:j + batch_size]
            results = await asyncio.gather(*[_ai_identify(b) for _, b in batch])
            for (h, _), ch in zip(batch, results):
                if ch and (ch.isdigit() or (ch.isascii() and ch.isalpha())):
                    await _save_by_hash(h, ch)
                    await cache.set(f"emoji:hash:{h}", ch)
                    total_new += 1

    print(f"\n\n完成！新识别 {total_new} 个，跳过已有 {total_skip} 个")
    if failed_packs:
        print(f"失败包: {len(failed_packs)} 个")

    # 最终统计
    final = await _get_existing_hashes()
    print(f"系统总映射: {len(final)} 个 MD5")

    await client.disconnect()
    await cache.close()


if __name__ == "__main__":
    asyncio.run(main())
