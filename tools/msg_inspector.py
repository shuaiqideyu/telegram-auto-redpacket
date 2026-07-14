"""消息结构检测器 —— 实时监听 Telegram 消息并输出完整字段结构。

用途：
  - 分析红包消息的按钮/字段/回调数据，辅助开发新 claimer
  - 调试 detector.classify() 的识别结果
  - 探索陌生 bot 的消息格式（reply_markup、entities 等）

用法：
  python -m tools.msg_inspector                  # 监听所有群组
  python -m tools.msg_inspector --chat -100123   # 只监听指定 chat
  python -m tools.msg_inspector --bot 5703356189 # 只监听指定 bot 的消息
  python -m tools.msg_inspector --json           # 同时输出 JSON 文件
  python -m tools.msg_inspector --all            # 显示所有消息（不限红包）
  python -m tools.msg_inspector --no-color       # 禁用终端颜色

需要：.env 里有 API_ID/API_HASH，且本地数据库有至少一个已登录账号的 session。
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    KeyboardButtonCallback,
    KeyboardButtonUrl,
    KeyboardButtonWebView,
    KeyboardButtonBuy,
    KeyboardButtonGame,
    KeyboardButtonRequestGeoLocation,
    KeyboardButtonRequestPhone,
    KeyboardButtonSwitchInline,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
    ReplyInlineMarkup,
    ReplyKeyboardMarkup,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import config
from core.detector import RedPacket, classify, clean, is_red_packet

log = logging.getLogger("msg_inspector")

# ── ANSI 颜色 ──────────────────────────────────────────────

_USE_COLOR = True


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else str(text)


def dim(t: str) -> str:    return _c("2", t)
def bold(t: str) -> str:   return _c("1", t)
def red(t: str) -> str:    return _c("91", t)
def green(t: str) -> str:  return _c("92", t)
def yellow(t: str) -> str: return _c("93", t)
def blue(t: str) -> str:   return _c("94", t)
def cyan(t: str) -> str:   return _c("96", t)
def mag(t: str) -> str:    return _c("95", t)
def bg_red(t: str) -> str: return _c("41;97", t)


# ── 字段提取 ────────────────────────────────────────────────

def _peer_str(peer) -> str:
    if peer is None:
        return "None"
    if isinstance(peer, PeerUser):
        return f"User({peer.user_id})"
    if isinstance(peer, PeerChat):
        return f"Chat({peer.chat_id})"
    if isinstance(peer, PeerChannel):
        return f"Channel({peer.channel_id})"
    return repr(peer)


def _entity_type(ent) -> str:
    _MAP = {
        MessageEntityBold: "bold",
        MessageEntityItalic: "italic",
        MessageEntityCode: "code",
        MessageEntityPre: "pre",
        MessageEntityUrl: "url",
        MessageEntityTextUrl: "text_url",
        MessageEntityMention: "mention",
        MessageEntityMentionName: "mention_name",
    }
    return _MAP.get(type(ent), type(ent).__name__)


def _btn_type_str(btn) -> str:
    _MAP = {
        KeyboardButtonCallback: "callback",
        KeyboardButtonUrl: "url",
        KeyboardButtonWebView: "webview",
        KeyboardButtonBuy: "buy",
        KeyboardButtonGame: "game",
        KeyboardButtonSwitchInline: "switch_inline",
        KeyboardButtonRequestPhone: "request_phone",
        KeyboardButtonRequestGeoLocation: "request_geo",
    }
    return _MAP.get(type(btn), type(btn).__name__)


def _format_callback_data(data: bytes) -> str:
    """callback data 的可读表示：先尝试 UTF-8，失败则 hex。"""
    if not data:
        return "b''"
    try:
        decoded = data.decode("utf-8")
        if decoded.isprintable():
            return repr(decoded)
    except (UnicodeDecodeError, ValueError):
        pass
    return data.hex()


def _media_summary(media) -> dict | None:
    if media is None:
        return None
    info = {"type": type(media).__name__}
    if isinstance(media, MessageMediaPhoto):
        photo = media.photo
        if photo:
            info["photo_id"] = photo.id
            if photo.sizes:
                biggest = max(photo.sizes, key=lambda s: getattr(s, "w", 0) * getattr(s, "h", 0), default=None)
                if biggest:
                    info["size"] = f"{getattr(biggest, 'w', '?')}x{getattr(biggest, 'h', '?')}"
    elif isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc:
            info["doc_id"] = doc.id
            info["mime"] = doc.mime_type
            info["size_bytes"] = doc.size
            for attr in doc.attributes:
                info[type(attr).__name__] = str(attr)
    elif isinstance(media, MessageMediaWebPage):
        wp = media.webpage
        if wp and hasattr(wp, "url"):
            info["url"] = wp.url
            info["title"] = getattr(wp, "title", None)
    return info


# ── 核心分析 ────────────────────────────────────────────────

def analyze_message(msg) -> dict:
    """把一条 Telethon Message 拆解为可序列化的字典。"""
    result = {
        "msg_id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "edit_date": msg.edit_date.isoformat() if msg.edit_date else None,
        "chat_id": msg.chat_id,
        "from_id": _peer_str(msg.from_id),
        "via_bot_id": msg.via_bot_id,
        "fwd_from": bool(msg.fwd_from),
        "reply_to_msg_id": getattr(msg.reply_to, "reply_to_msg_id", None) if msg.reply_to else None,
        "text": msg.text,
        "raw_text": msg.raw_text if msg.raw_text != msg.text else "(same as text)",
    }

    # entities
    if msg.entities:
        result["entities"] = []
        for ent in msg.entities:
            e = {"type": _entity_type(ent), "offset": ent.offset, "length": ent.length}
            if isinstance(ent, MessageEntityTextUrl):
                e["url"] = ent.url
            elif isinstance(ent, MessageEntityMentionName):
                e["user_id"] = ent.user_id
            elif isinstance(ent, MessageEntityPre):
                e["language"] = ent.language
            result["entities"].append(e)

    # reply_markup（重点）
    markup = msg.reply_markup
    if markup:
        markup_info = {"type": type(markup).__name__, "rows": []}
        rows = getattr(markup, "rows", None) or []
        for ri, row in enumerate(rows):
            row_btns = []
            for bi, btn in enumerate(row.buttons):
                b = {
                    "type": _btn_type_str(btn),
                    "text_raw": btn.text,
                    "text_cleaned": clean(btn.text),
                    "position": f"[{ri}][{bi}]",
                }
                if isinstance(btn, KeyboardButtonCallback):
                    b["data_readable"] = _format_callback_data(btn.data)
                    b["data_hex"] = btn.data.hex() if btn.data else None
                    b["data_bytes"] = len(btn.data) if btn.data else 0
                elif isinstance(btn, KeyboardButtonUrl):
                    b["url"] = btn.url
                elif isinstance(btn, KeyboardButtonWebView):
                    b["url"] = btn.url
                elif isinstance(btn, KeyboardButtonSwitchInline):
                    b["query"] = btn.query
                    b["same_peer"] = btn.same_peer
                row_btns.append(b)
            markup_info["rows"].append(row_btns)
        result["reply_markup"] = markup_info

    # media
    result["media"] = _media_summary(msg.media)

    # 红包分析
    rp_analysis = {}
    text = msg.text or msg.raw_text or ""
    rp_analysis["is_red_packet"] = is_red_packet(text)
    rp_analysis["matched_keywords"] = [kw for kw in config.red_packet_keywords if kw in text]

    rp = classify(msg)
    if rp:
        rp_analysis["classify_result"] = {
            "kind": rp.kind,
            "direct_data": _format_callback_data(rp.direct_data) if rp.direct_data else None,
            "start_param": rp.start_param,
            "captcha_expr": rp.captcha_expr,
            "captcha_buttons": [(lbl, _format_callback_data(d)) for lbl, d in rp.captcha_buttons] if rp.captcha_buttons else None,
        }
    else:
        rp_analysis["classify_result"] = None

    result["red_packet"] = rp_analysis
    return result


# ── 终端美化输出 ─────────────────────────────────────────────

_SEP = "─" * 72


def _print_section(title: str, content_lines: list[str]):
    print(f"  {bold(cyan(title))}")
    for line in content_lines:
        print(f"    {line}")


def print_analysis(data: dict, chat_title: str = ""):
    ts = data.get("date", "")
    is_rp = data["red_packet"]["is_red_packet"]
    classify_result = data["red_packet"]["classify_result"]

    # 头部分隔线
    if is_rp:
        header = bg_red(" 🧧 红包消息 ")
        if classify_result:
            header += f" → {bold(yellow(classify_result['kind'].upper()))}"
    else:
        header = dim("📨 普通消息")

    print(f"\n{dim(_SEP)}")
    print(f"  {header}  {dim(ts)}")
    print(f"  {dim('chat:')} {green(chat_title or str(data['chat_id']))}  "
          f"{dim('msg_id:')} {data['msg_id']}  "
          f"{dim('from:')} {data['from_id']}")
    if data.get("via_bot_id"):
        print(f"  {dim('via_bot:')} {yellow(str(data['via_bot_id']))}")
    if data.get("edit_date"):
        print(f"  {dim('edit_date:')} {yellow(data['edit_date'])}")

    # 文本
    text = data.get("text") or ""
    if text:
        lines = text.split("\n")
        display = lines[:8]
        if len(lines) > 8:
            display.append(dim(f"... ({len(lines) - 8} more lines)"))
        _print_section("TEXT", display)

    # entities
    if data.get("entities"):
        ent_lines = []
        for e in data["entities"]:
            extra = ""
            if e.get("url"):
                extra = f" → {blue(e['url'])}"
            elif e.get("user_id"):
                extra = f" → user:{e['user_id']}"
            ent_lines.append(f"{e['type']:15s} offset={e['offset']} len={e['length']}{extra}")
        _print_section("ENTITIES", ent_lines)

    # reply_markup（核心重点）
    markup = data.get("reply_markup")
    if markup:
        btn_lines = [f"type: {markup['type']}"]
        for ri, row in enumerate(markup["rows"]):
            for btn in row:
                pos = btn["position"]
                btype = btn["type"]
                raw_text = btn["text_raw"]
                cleaned = btn["text_cleaned"]

                line = f"{dim(pos)} {yellow(btype):20s} "
                line += f'text: "{raw_text}"'
                if cleaned != raw_text.replace(" ", ""):
                    line += f"  {dim('cleaned:')} \"{mag(cleaned)}\""

                if btn.get("data_readable"):
                    line += f"\n           {dim('data:')} {cyan(btn['data_readable'])}"
                    line += f"  {dim('hex:')} {btn.get('data_hex', '')}"
                    nbytes = btn.get("data_bytes", 0)
                    line += f"  {dim(f'({nbytes} bytes)')}"
                if btn.get("url"):
                    line += f"\n           {dim('url:')} {blue(btn['url'])}"
                if btn.get("query") is not None:
                    line += f"\n           {dim('query:')} \"{btn['query']}\""
                btn_lines.append(line)
        _print_section("REPLY_MARKUP", btn_lines)

    # media
    if data.get("media"):
        media_lines = [f"{k}: {v}" for k, v in data["media"].items()]
        _print_section("MEDIA", media_lines)

    # 红包分析
    rp = data["red_packet"]
    if rp["is_red_packet"] or classify_result:
        rp_lines = []
        rp_lines.append(f"is_red_packet: {green('YES') if rp['is_red_packet'] else red('NO')}")
        if rp["matched_keywords"]:
            rp_lines.append(f"matched_keywords: {rp['matched_keywords']}")
        if classify_result:
            cr = classify_result
            rp_lines.append(f"kind: {bold(yellow(cr['kind']))}")
            if cr["direct_data"]:
                rp_lines.append(f"direct_data: {cyan(cr['direct_data'])}")
            if cr["start_param"]:
                rp_lines.append(f"start_param: {cyan(cr['start_param'])}")
            if cr["captcha_expr"]:
                rp_lines.append(f"captcha_expr: {cyan(cr['captcha_expr'])}")
            if cr.get("captcha_buttons"):
                for lbl, d in cr["captcha_buttons"]:
                    rp_lines.append(f"  option: [{mag(lbl)}] → {d}")
        else:
            rp_lines.append(f"classify: {dim('None（无可领按钮或不匹配）')}")
        _print_section("🔍 RED PACKET ANALYSIS", rp_lines)

    print(dim(_SEP))


# ── 获取 Session ────────────────────────────────────────────

async def _get_session_from_db() -> str | None:
    """从数据库取第一个可用的已登录账号 session。"""
    try:
        from backend.db import async_session
        from backend.models import Account
        from sqlalchemy import select
        from core.crypto import decrypt_session

        async with async_session() as sess:
            row = (await sess.execute(
                select(Account).where(Account.logged_in.is_(True)).limit(1)
            )).scalar_one_or_none()
            if row and row.session_string:
                return decrypt_session(row.session_string)
    except Exception as e:
        log.debug(f"从 DB 读 session 失败: {e}")
    return None


async def _get_session(session_arg: str | None) -> str | None:
    """优先用命令行传入的 session，其次从 DB 读。"""
    if session_arg:
        return session_arg
    return await _get_session_from_db()


# ── 主循环 ──────────────────────────────────────────────────

async def run(args):
    session_str = await _get_session(args.session)
    if not session_str:
        print(red("❌ 未找到可用 session。请通过 --session 传入，或确保数据库中有已登录账号。"))
        return

    client = TelegramClient(StringSession(session_str), config.api_id, config.api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(red("❌ session 已失效，请重新登录。"))
        return

    me = await client.get_me()
    print(f"\n{bold('📡 消息结构检测器')} — 已登录: {green(me.first_name)} (ID:{me.id})")
    print(dim(f"   API_ID: {config.api_id}"))

    filter_info = []
    if args.chat:
        filter_info.append(f"chat={args.chat}")
    if args.bot:
        filter_info.append(f"bot={args.bot}")
    if args.all:
        filter_info.append("显示所有消息")
    else:
        filter_info.append("仅红包相关消息（加 --all 显示全部）")

    print(f"   过滤: {', '.join(filter_info)}")

    if args.json:
        json_path = ROOT / f"msg_dump_{int(time.time())}.json"
        print(f"   JSON 输出: {blue(str(json_path))}")
    else:
        json_path = None

    json_records: list[dict] = []

    # 群组列表
    chat_names: dict[int, str] = {}
    try:
        async for d in client.iter_dialogs():
            if d.is_group or d.is_channel:
                chat_names[d.id] = d.title
    except Exception:
        pass

    count = 0
    print(f"\n{bold('⏳ 开始监听...')} Ctrl+C 退出\n")

    async def on_message(event):
        nonlocal count
        msg = event.message

        # 过滤：指定 chat
        if args.chat and msg.chat_id not in args.chat:
            return

        # 过滤：指定 bot
        if args.bot:
            from_id = getattr(msg.from_id, "user_id", None) if msg.from_id else None
            via = msg.via_bot_id
            if from_id not in args.bot and via not in args.bot:
                return

        # 过滤：默认只显示有 reply_markup 的或红包相关的
        text = msg.text or msg.raw_text or ""
        has_markup = msg.reply_markup is not None
        if not args.all and not has_markup and not is_red_packet(text):
            return

        count += 1
        chat_title = chat_names.get(msg.chat_id, "")
        if not chat_title:
            try:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or ""
                if chat_title:
                    chat_names[msg.chat_id] = chat_title
            except Exception:
                pass

        data = analyze_message(msg)
        print_analysis(data, chat_title)
        print(dim(f"  [#{count}]"))

        if json_path is not None:
            json_records.append(data)

    client.add_event_handler(on_message, events.NewMessage())
    client.add_event_handler(on_message, events.MessageEdited())

    try:
        await client.run_until_disconnected()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if json_path and json_records:
            json_path.write_text(json.dumps(json_records, ensure_ascii=False, indent=2, default=str))
            print(f"\n{green('✅')} 已保存 {len(json_records)} 条记录到 {json_path}")
        await client.disconnect()
        print(f"\n{dim('已断开，共捕获')} {bold(str(count))} {dim('条消息')}")


# ── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Telegram 消息结构检测器 — 实时分析红包消息字段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python -m tools.msg_inspector                   监听所有群组（仅含按钮/红包的消息）
  python -m tools.msg_inspector --all             显示所有消息
  python -m tools.msg_inspector --chat -100123    只监听指定 chat_id
  python -m tools.msg_inspector --bot 5703356189  只看 OKPay bot 的消息
  python -m tools.msg_inspector --json            同时保存 JSON 文件
""")
    parser.add_argument("--session", help="Telethon StringSession（不传则从 DB 读第一个已登录账号）")
    parser.add_argument("--chat", type=int, nargs="+", help="只监听指定 chat_id（可多个）")
    parser.add_argument("--bot", type=int, nargs="+", help="只看指定 bot_id 发送/via 的消息")
    parser.add_argument("--all", action="store_true", help="显示所有消息（默认只显示有按钮或红包关键词的）")
    parser.add_argument("--json", action="store_true", help="退出时保存 JSON 到项目根")
    parser.add_argument("--no-color", action="store_true", help="禁用终端 ANSI 颜色")
    parser.add_argument("--log-level", default="WARNING", help="日志级别（默认 WARNING，调试用 DEBUG）")

    args = parser.parse_args()

    global _USE_COLOR
    if args.no_color or not sys.stdout.isatty():
        _USE_COLOR = False

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
