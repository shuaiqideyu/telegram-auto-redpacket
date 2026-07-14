"""模块1 关键词领取：点击群内含关键词的 callback 按钮即到账，无验证码，最快路径。

跨账号共享：callback data 是固定 bytes，检测账号提取后所有账号共用，
其他账号无需 re-fetch 群消息，直接用 (chat_id, msg_id, data) 点击。

判定优先级：
1. EMPTY_KW / BLOCKED_KW 命中 → 失败（不重试）
2. 提取到金额 → 成功（兼容所有钱包）
3. SUCCESS_KW 命中 → 成功
4. 有回调文本但无法判定 → 视为成功（乐观策略，展示原文）
5. 无回调文本 → 可重试
"""
import logging
import time

from telethon.tl import functions
from telethon.tl.types import Message

from .base import BLOCKED_KW, EMPTY_KW, SUCCESS_KW, ClaimResult, extract_amount

log = logging.getLogger("core.direct")


class DirectClaimer:
    def __init__(self, client):
        self.client = client

    async def claim(self, event, msg: Message, data: bytes, tm: dict) -> ClaimResult:
        """检测账号本地领取：复用 event 的 input_chat（最快，免解析 peer）。"""
        tm["mode"] = "direct"
        try:
            peer = await event.get_input_chat()
            res = await self.client(functions.messages.GetBotCallbackAnswerRequest(
                peer=peer, msg_id=msg.id, data=data))
            tm["submit"] = time.time()
            text = res.message or ""
        except Exception as e:
            log.debug(f"直接领取异常: {e}")
            return ClaimResult(False, retryable=True)
        return self._judge(text, tm)

    async def claim_fast(self, chat_id: int, msg_id: int, data: bytes, tm: dict) -> ClaimResult:
        """跨账号快路径：仅凭 (chat_id, msg_id, data) 点击，不需要群消息对象。

        callback 只需 peer + msg_id + data，省去 get_messages 的 100-300ms 往返。"""
        tm["mode"] = "direct"
        try:
            peer = await self.client.get_input_entity(chat_id)
            res = await self.client(functions.messages.GetBotCallbackAnswerRequest(
                peer=peer, msg_id=msg_id, data=data))
            tm["submit"] = time.time()
            text = res.message or ""
        except Exception as e:
            log.debug(f"直接领取异常(fast): {e}")
            return ClaimResult(False, retryable=True)
        return self._judge(text, tm)

    def _judge(self, text: str, tm: dict) -> ClaimResult:
        """统一判定 callback 反馈文本 → ClaimResult。"""
        tm["feedback"] = text
        amount = extract_amount(text)

        # 1) 已被领完/门槛不满足 → 立即失败
        if any(k in text for k in EMPTY_KW):
            log.info(f"🈳 {text.strip()}")
            return ClaimResult(False, amount, retryable=False)
        if any(k in text for k in BLOCKED_KW):
            log.info(f"🚫 {text.strip()}")
            return ClaimResult(False, amount, retryable=False)

        # 2) 提取到金额 → 成功
        if amount:
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "direct", retryable=False)

        # 3) 关键词命中 → 成功
        if any(k in text for k in SUCCESS_KW):
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "direct", retryable=False)

        # 4) 有回调文本但无法判定 → 乐观视为成功，展示原文
        if text:
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, "direct", retryable=False)

        # 5) 完全无反馈 → 可重试
        return ClaimResult(False, retryable=True)
