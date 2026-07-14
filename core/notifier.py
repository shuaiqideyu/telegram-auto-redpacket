"""通知机器人：领取状态由 bot 私聊推送给账号本人。

为什么用 bot 推送而不是账号自己发：
- 账号在群里自己发"已领取"会暴露开挂，bot 私聊只有本人能看到。
- 同一系统多账号共用一个 bot，bot 天然做消息归集（按 user_id 分发）。

绑定原理（Telegram 限制：bot 不能主动私聊从未交互过的用户）：
- 登录的 telethon 账号自动给 bot 发 /start，建立会话；
- 之后用 Bot API 以纯数字 user_id 私聊推送（无需 access_hash，最稳）。
"""
import logging

import httpx

from .config import config

log = logging.getLogger("core.notify")


class Notifier:
    def __init__(self, token: str = ""):
        self.token = token or config.notify_bot_token
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.enabled = bool(self.token)
        self.bot_username: str | None = None
        self.user_id: int | None = None  # 接收通知的账号本人 id
        self._http: httpx.AsyncClient | None = None

    async def bind(self, user_client, me=None) -> bool:
        """启动机器人并把当前登录账号绑定到它（账号自动 /start，幂等）。"""
        if not self.enabled:
            log.warning("未配置 NOTIFY_BOT_TOKEN，领取通知将不发送")
            return False
        self._http = httpx.AsyncClient(timeout=10.0)
        try:
            bot = await self._get_me()
            self.bot_username = bot.get("username")
            if me is None:
                me = await user_client.get_me()
            self.user_id = me.id
            # 账号给 bot 发 /start，建立私聊会话，使 bot 能主动推送
            await user_client.send_message(self.bot_username, "/start")
            log.debug(f"🤖 通知机器人 @{self.bot_username} 已绑定账号 {me.first_name}({me.id})")
            return True
        except Exception as e:
            log.error(f"绑定通知机器人失败: {e}")
            self.enabled = False
            return False

    async def _get_me(self) -> dict:
        r = await self._http.get(f"{self.api}/getMe")
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "getMe 失败"))
        return data["result"]

    async def send(self, text: str, user_id: int | None = None) -> bool:
        """私聊推送通知给指定账号（默认绑定的本人）。"""
        if not self.enabled or not self._http:
            return False
        uid = user_id or self.user_id
        if not uid:
            return False
        try:
            r = await self._http.post(
                f"{self.api}/sendMessage",
                json={
                    "chat_id": uid,
                    "text": text,
                    "link_preview_options": {"is_disabled": True},
                },
            )
            if not r.json().get("ok"):
                log.debug(f"通知发送失败: {r.text}")
                return False
            return True
        except Exception as e:
            log.debug(f"通知发送异常: {e}")
            return False

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None


async def broadcast_channel(token: str, channel_id: int, text: str,
                            buttons: list[dict] | None = None) -> bool:
    """用 bot 向通信频道发消息。buttons: [{"text": "xx", "url": "xx"}, ...]"""
    if not token or not channel_id:
        return False
    payload: dict = {
        "chat_id": channel_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[btn] for btn in buttons]
        }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            return bool(r.json().get("ok"))
    except Exception as e:
        log.debug(f"通信频道广播失败: {e}")
        return False
