"""模块2 窗口验证码：群内消息含算式，按钮是选项，解题后点对应按钮。

okpay / kkpay / wlqb 等钱包的群内算式验证码（X + Y = ?），答案对所有账号固定。
跨账号共享：
- 答案经 Redis（rp_answer:{chat}:{msg}）共享，一个账号解出后其他账号直接复用
- 按钮 callback data 固定，其他账号无需 re-fetch 群消息，直接点击

字符归一化、算式求解等通用逻辑见 core/charset.py。
"""
import logging
import time

from telethon.tl import functions
from telethon.tl.types import Message

from ..charset import (
    CONFUSIONS,
    buttons_are_digit,
    coerce,
    compute,
    normalize_operand,
    solve_expr,
    solve_expr_with_buttons,
    EXPR_RE,
    FULLWIDTH_OP,
)
from .base import BLOCKED_KW, EMPTY_KW, SUCCESS_KW, WRONG_KW, ClaimResult, extract_amount

log = logging.getLogger("core.captcha")

# 向后兼容：外部模块 import 的旧名称
_normalize_operand = normalize_operand
_buttons_are_digit = buttons_are_digit


class CaptchaClaimer:
    def __init__(self, client):
        self.client = client

    async def claim(
        self,
        event,
        msg: Message,
        expr: str,
        buttons: list[tuple[str, bytes]],
        tm: dict,
    ) -> ClaimResult:
        """检测账号本地领取：复用 event 的 input_chat（最快，免解析 peer）。"""
        return await self._do_claim(msg.chat_id, msg.id, expr, buttons, tm, event=event)

    async def claim_fast(
        self,
        chat_id: int,
        msg_id: int,
        expr: str,
        buttons: list[tuple[str, bytes]],
        tm: dict,
    ) -> ClaimResult:
        """跨账号快路径：仅凭 (chat_id, msg_id, 算式, 按钮) 领取，不需要群消息对象。

        答案走 Redis 共享，按钮 data 已在 rp 中，省去 get_messages 往返。"""
        return await self._do_claim(chat_id, msg_id, expr, buttons, tm, event=None)

    async def _do_claim(self, chat_id, msg_id, expr, buttons, tm, event=None) -> ClaimResult:
        tm["mode"] = "captcha"

        # 验证码答案共享：同一红包其他账号已解出 → 直接用，跳过解题
        ans_key = f"rp_answer:{chat_id}:{msg_id}"
        answer = None
        try:
            from core import cache
            answer = await cache.get(ans_key)
        except Exception:
            pass

        if answer:
            log.info(f"♻️ 复用答案 {answer}（{expr}）")
        else:
            btn_labels = [l for l, _ in buttons]
            answer, corrections = solve_expr_with_buttons(expr, btn_labels)
            if answer is None:
                log.warning(f"无法解析算式: {expr!r}")
                return ClaimResult(False, retryable=False)
            if corrections:
                tm["corrections"] = corrections
            # 解出的答案存 Redis 供其他账号复用
            try:
                from core import cache
                await cache.set(ans_key, answer, ex=60)
            except Exception:
                pass
            log.info(f"🧮 {expr} → {answer}")

        tm["solve"] = time.time()

        target_data = None
        answer_upper = answer.upper()
        for label, data in buttons:
            norm_label = _normalize_operand(label).upper()
            # 精确匹配 → 去前导零匹配 → 数值匹配
            if norm_label == answer_upper:
                target_data = data
                break
            if norm_label.lstrip("0") == answer_upper.lstrip("0") and answer_upper.lstrip("0"):
                target_data = data
                break

        if target_data is None:
            log.warning(f"答案 {answer} 不在选项中: {[l + '→' + _normalize_operand(l) for l, _ in buttons]}")
            return ClaimResult(False, retryable=False)

        try:
            if event is not None:
                peer = await event.get_input_chat()
            else:
                peer = await self.client.get_input_entity(chat_id)
            res = await self.client(functions.messages.GetBotCallbackAnswerRequest(
                peer=peer, msg_id=msg_id, data=target_data))
            tm["submit"] = time.time()
            text = res.message or ""
        except Exception as e:
            log.debug(f"验证码领取异常: {e}")
            return ClaimResult(False, retryable=True)

        tm["feedback"] = text
        amount = extract_amount(text)

        if any(k in text for k in WRONG_KW):
            # 验证码答错 = emoji 识别错误，清掉错误答案缓存 + 标记重新识别
            log.warning(f"❌ 验证码错误，答案 {answer} 有误，将重新识别")
            try:
                from core import cache
                await cache.delete(ans_key)
            except Exception:
                pass
            tm["wrong_captcha"] = True
            return ClaimResult(False, amount, retryable=True)
        if any(k in text for k in EMPTY_KW):
            log.info(f"🈳 {text.strip()}")
            return ClaimResult(False, amount, retryable=False)
        if any(k in text for k in BLOCKED_KW):
            log.info(f"🚫 {text.strip()}")
            return ClaimResult(False, amount, retryable=False)
        if amount:
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "captcha", retryable=False)
        if any(k in text for k in SUCCESS_KW):
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "captcha", retryable=False)
        if text:
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "captcha", retryable=False)

        return ClaimResult(False, retryable=True)
