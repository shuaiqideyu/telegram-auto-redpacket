"""模块4 私信验证码：群内红包跳转 bot 私聊领取（wlqb 等）。

可共享的只有从群消息提取的 start_param。/start 后 bot 的私聊回执有两种形态，
本模块都兼容：
- A) 直接到账：bot 收到 /start 立即回 `🧧领取红包 +60.72 WLCOIN`，无需再解题
- B) 私聊出题：bot 先回算式 + callback 选项，需解题点按钮后才到账

流程：
1. 群消息含红包关键词 + bot URL 按钮（t.me/bot?start=xxx），无群内 callback 选项
2. 给 bot 发 /start xxx
3. 竞速等待：先到「到账回执」→ 直接成功(A)；先到「算式题」→ 解题点按钮(B)
"""
import asyncio
import logging
import time

from telethon import events
from telethon.tl import functions
from telethon.tl.types import KeyboardButtonCallback

from ..charset import MATH_RE, normalize_operand, solve_expr_with_buttons
from .base import BLOCKED_KW, EMPTY_KW, SUCCESS_KW, WRONG_KW, ClaimResult, extract_amount

log = logging.getLogger("core.dm_captcha")


def _parse_dm_quiz(msg) -> tuple[str, list[tuple[str, bytes]]] | None:
    """从私聊消息中提取算式 + callback 按钮选项。"""
    text = getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
    m = MATH_RE.search(text)
    if not m:
        return None
    expr = m.group(0)
    buttons: list[tuple[str, bytes]] = []
    for row in getattr(getattr(msg, "reply_markup", None), "rows", None) or []:
        for btn in row.buttons:
            if isinstance(btn, KeyboardButtonCallback):
                label = btn.text.strip()
                if label:
                    buttons.append((label, btn.data))
    return (expr, buttons) if buttons else None


class DmCaptchaClaimer:
    """私信验证码领取器：群内触发 → 私聊解题。"""

    def __init__(self, client):
        self.client = client

    async def claim(self, bot_username: str, start_param: str, tm: dict) -> ClaimResult:
        tm["mode"] = "dm_captcha"

        bot_entity = await self.client.get_input_entity(bot_username)
        bot_full = await self.client.get_entity(bot_entity)
        bot_id = bot_full.id

        loop = asyncio.get_running_loop()
        captcha_fut: asyncio.Future = loop.create_future()
        result_fut: asyncio.Future = loop.create_future()

        async def _on_dm(ev):
            msg = ev.message
            quiz = _parse_dm_quiz(msg)
            if quiz and not captcha_fut.done():
                captcha_fut.set_result((msg, quiz))
                return
            text = msg.text or ""
            if not result_fut.done():
                # 顺序要紧：「已领取过红包」含「领取红包」，EMPTY/BLOCKED 必须先判
                if any(k in text for k in EMPTY_KW):
                    result_fut.set_result(("empty", text))
                elif any(k in text for k in BLOCKED_KW):
                    result_fut.set_result(("blocked", text))
                elif any(k in text for k in (*SUCCESS_KW, "🎉", "🧧")):
                    result_fut.set_result(("ok", text))

        self.client.add_event_handler(_on_dm, events.NewMessage(from_users=bot_id))
        try:
            await self.client.send_message(bot_entity, f"/start {start_param}")
            tm["start_sent"] = time.time()

            # 竞速：到账回执 vs 私聊算式题，谁先来用谁
            done, pending = await asyncio.wait(
                {captcha_fut, result_fut}, timeout=8.0,
                return_when=asyncio.FIRST_COMPLETED)

            # A) 直接到账（无需解题）
            if result_fut in done:
                status, dm_text = result_fut.result()
                amount = extract_amount(dm_text)
                if status == "ok":
                    tm["submit"] = tm["amount"] = time.time()
                    log.info(f"💰 {dm_text.strip()}")
                    return ClaimResult(True, amount, "dm_captcha", retryable=False)
                if status in ("empty", "blocked"):
                    log.info(f"🈳 {dm_text.strip()}")
                    return ClaimResult(False, amount, retryable=False)

            # B) 私聊出题 → 走解题流程
            if captcha_fut not in done:
                log.debug("未收到私聊验证码/到账回执")
                return ClaimResult(False, retryable=True)
            dm_msg, (expr, buttons) = captcha_fut.result()
            tm["captcha_received"] = time.time()

            # 解题（复用 captcha 模块逻辑）
            btn_labels = [l for l, _ in buttons]
            answer, _ = solve_expr_with_buttons(expr, btn_labels)
            if answer is None:
                log.warning(f"无法解析私聊算式: {expr!r}")
                return ClaimResult(False, retryable=False)
            log.info(f"🧮 私聊验证: {expr} → {answer}")
            tm["solve"] = time.time()

            # 匹配按钮
            target_data = None
            answer_upper = answer.upper()
            for label, data in buttons:
                norm = normalize_operand(label).upper()
                if norm == answer_upper:
                    target_data = data
                    break
                if norm.lstrip("0") == answer_upper.lstrip("0") and answer_upper.lstrip("0"):
                    target_data = data
                    break

            if target_data is None:
                log.warning(f"答案 {answer} 不在私聊选项中: {[l for l, _ in buttons]}")
                return ClaimResult(False, retryable=False)

            # 点击答案
            try:
                peer = await self.client.get_input_entity(bot_id)
                res = await self.client(functions.messages.GetBotCallbackAnswerRequest(
                    peer=peer, msg_id=dm_msg.id, data=target_data))
                tm["submit"] = time.time()
                text = res.message or ""
            except Exception as e:
                log.debug(f"私聊点击异常: {e}")
                return ClaimResult(False, retryable=True)

            tm["feedback"] = text
            amount = extract_amount(text)

            if any(k in text for k in WRONG_KW):
                log.warning(f"❌ 私聊验证码错误: {answer}")
                return ClaimResult(False, amount, retryable=True)
            if any(k in text for k in EMPTY_KW):
                return ClaimResult(False, amount, retryable=False)
            if any(k in text for k in BLOCKED_KW):
                return ClaimResult(False, amount, retryable=False)
            if amount or any(k in text for k in SUCCESS_KW):
                tm["amount"] = time.time()
                log.info(f"💰 {text.strip()}")
                return ClaimResult(True, amount, "dm_captcha", retryable=False)

            # callback 无文本 → 等后续私聊到账反馈
            if not text:
                try:
                    status, dm_text = await asyncio.wait_for(result_fut, 10.0)
                except asyncio.TimeoutError:
                    status, dm_text = "timeout", ""
                amount = extract_amount(dm_text)
                if status == "ok":
                    tm["amount"] = time.time()
                    log.info(f"💰 {dm_text.strip()}")
                    return ClaimResult(True, amount, "dm_captcha", retryable=False)
                if status in ("empty", "blocked"):
                    return ClaimResult(False, amount, retryable=False)

            # 有回复但无法归类 → 视为到账
            if text:
                tm["amount"] = time.time()
                return ClaimResult(True, amount, "dm_captcha", retryable=False)

            return ClaimResult(False, retryable=True)

        finally:
            self.client.remove_event_handler(_on_dm)
