"""监听调度：检测红包 → 分流到 direct / webapp 模块 → 去重重试 → 回发报表。

去重与重试：
- _done     已抢到/已被领完 → 不再尝试
- _inflight 正在抢 → 防编辑事件并发堆叠
- _attempts 每个红包尝试次数 → 限定上限
不追踪锁定状态——每次只看当前按钮，能领就领。
"""
import asyncio
import logging
import time
from collections import deque

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from .browser import launch_browser
from .claimers.base import BLOCKED_KW, ClaimResult, derive_error_reason
from .claimers.captcha import CaptchaClaimer
from .claimers.direct import DirectClaimer
from .claimers.dm_captcha import DmCaptchaClaimer
from .claimers.fulilai import FulilaiClaimer
from .claimers.webapp import WebappClaimer
from .config import RunConfig
from .detector import KIND_LABELS, classify, find_captcha_quiz, is_red_packet
from .emoji_decoder import decode_custom_emoji, extract_custom_emoji_ids
from .filters import check_filters
from .notifier import Notifier
from .reporter import build_report
from .vision.solver import CaptchaSolver

log = logging.getLogger("core.grabber")

_KIND_LABEL = KIND_LABELS  # 唯一文案来源在 detector.KIND_LABELS

# 重试间隔：抢红包要快，仅留极小退避避免空转（不做无谓延迟）
_RETRY_DELAY = 0.003

# 本地去重集合容量上限：Redis 已按 TTL 全局去重，本地仅兜底，超阈值清空防长跑内存增长
_DEDUP_CAP = 4000


def _parse_proxy(url: str | None):
    """'socks5://user:pass@host:port' → python-socks 元组（Telethon proxy 参数）。

    返回 (scheme, host, port) 或带认证的 (scheme, host, port, True, user, pass)；
    无配置/解析失败返回 None（直连）。需要 python-socks（见 requirements）。
    """
    if not url or not url.strip():
        return None
    from urllib.parse import urlparse
    try:
        p = urlparse(url.strip())
        scheme = (p.scheme or "socks5").lower()
        if scheme == "socks5h":
            scheme = "socks5"
        if scheme not in ("socks5", "socks4", "http"):
            return None
        if not p.hostname or not p.port:
            return None
        if p.username or p.password:
            return (scheme, p.hostname, p.port, True, p.username or "", p.password or "")
        return (scheme, p.hostname, p.port)
    except Exception:
        return None


class OKPayGrabber:
    def __init__(self, run: RunConfig | None = None, on_record=None, on_ready=None,
                 on_redpacket=None):
        self.run = run or RunConfig.from_config()
        self.on_record = on_record
        self.on_ready = on_ready  # async callback(grabber) 连接就绪后调用
        # async callback(chat_id, msg_id, rp, chat_title, msg)：检测到红包交给 Runner 统一调度
        self.on_redpacket = on_redpacket
        if not self.run.session:
            raise ValueError("缺少账号 StringSession：请先通过 Web 端登录再启动监听")
        proxy = _parse_proxy(self.run.proxy)
        if self.run.proxy and not proxy:
            log.warning(f"代理格式无法解析，将直连: {self.run.proxy}")
        self.client = TelegramClient(
            StringSession(self.run.session), self.run.api_id, self.run.api_hash,
            proxy=proxy)
        self.solver = CaptchaSolver(
            self.run.vision_api_key, self.run.vision_base_url, self.run.vision_model)
        self.notifier = Notifier(self.run.notify_bot_token)
        self.me = None
        self.groups_count = 0  # 监听的群组/频道数（窗口数）
        self.groups: list[dict] = []  # 群组列表 [{chat_id, title, username, members_count}]
        self.stats = {"detected": 0, "success": 0, "failed": 0}
        self.history: deque = deque(maxlen=100)

        self._browser = None
        self._browser_close = None
        self._direct: DirectClaimer | None = None
        self._captcha: CaptchaClaimer | None = None
        self._dm_captcha: DmCaptchaClaimer | None = None
        self._webapp: WebappClaimer | None = None
        self._fulilai: FulilaiClaimer | None = None

        self._done: set = set()
        self._inflight: set = set()
        self._attempts: dict = {}
        self._chat_blocked: dict = {}  # chat_id -> 截止时间

    # ---- 生命周期 ----
    async def start(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            log.error("session 失效，请在 Web 端重新登录该账号")
            return

        me = await self.client.get_me()
        self.me = me
        log.info(f"已登录: {me.first_name} (ID:{me.id})")

        # 先注册事件处理器，再做耗时初始化（确保红包不漏）
        self.client.add_event_handler(self._on_message, events.NewMessage())
        self.client.add_event_handler(self._on_message, events.MessageEdited())
        self._direct = DirectClaimer(self.client)
        self._captcha = CaptchaClaimer(self.client)
        self._dm_captcha = DmCaptchaClaimer(self.client)
        if self.run.twocaptcha_key:
            fulilai_cfg = self.run.module_configs.get("fulilai", {})
            pool_size = max(1, int(fulilai_cfg.get("pool_size") or "1"))
            pool_enabled = fulilai_cfg.get("pool_enabled", "1") != "0"
            self._fulilai = FulilaiClaimer(self.client, self.run.twocaptcha_key, pool_size=pool_size)
            if pool_enabled:
                asyncio.create_task(self._fulilai.start())

        # 昵称同步回调（非阻塞）
        if self.on_ready:
            try:
                await self.on_ready(self)
            except Exception:
                pass

        # 耗时操作放后面，不阻塞红包监听
        asyncio.create_task(self._async_init())

        await self.client.run_until_disconnected()

    async def _async_init(self):
        """耗时初始化（通知绑定、浏览器、列群），不阻塞主监听。"""
        try:
            await self.notifier.bind(self.client, self.me)
        except Exception as e:
            log.debug(f"通知绑定失败: {e}")
        try:
            self._browser, self._browser_close = await launch_browser(self.run.chrome_path)
            self._webapp = WebappClaimer(self.client, self.solver, self._browser, self.run.vision_models)
            log.debug(f"🟢 就绪（模块: {self.run.modules}）")
        except Exception as e:
            log.warning(f"浏览器启动失败（webapp 模块不可用）: {e}")
        await self._list_groups()

    async def stop(self):
        try:
            await self.notifier.close()
            if self._browser_close:
                await self._browser_close()
        finally:
            if self.client.is_connected():
                await self.client.disconnect()

    async def _list_groups(self):
        try:
            groups: list[dict] = []
            async for d in self.client.iter_dialogs():
                if d.is_group or d.is_channel:
                    ent = d.entity
                    # 广播频道: is_channel=True 且 is_group=False；超级群/普通群归为 group
                    chat_type = "channel" if (d.is_channel and not d.is_group) else "group"
                    groups.append({
                        "chat_id": d.id,
                        "title": getattr(ent, "title", None) or d.name or "",
                        "username": getattr(ent, "username", None) or "",
                        "members_count": getattr(ent, "participants_count", None),
                        "chat_type": chat_type,
                    })
            self.groups = groups
            self.groups_count = len(groups)
            log.debug(f"📋 可监听 {len(groups)} 个群组/频道")
        except Exception as e:
            log.debug(f"列群失败: {e}")

    async def scan_groups(self) -> list[dict]:
        """实时扫描当前账号的群组/频道列表（供 Web 端汇总）。"""
        await self._list_groups()
        return self.groups

    # ---- 调度 ----
    def _chat_allowed(self, chat_id: int) -> bool:
        # 每群独立开关：默认开启，只有显式关闭的群才忽略
        return chat_id not in self.run.disabled_chat_ids

    def _blocked(self, event, msg) -> str | None:
        """屏蔽规则命中检测（私信/群/频道/用户/机器人）。返回命中原因，None=放行。"""
        run = self.run
        if run.block_private and getattr(event, "is_private", False):
            return "私信"
        cid = getattr(msg, "chat_id", None)
        if cid is not None and cid in run.blocked_chat_ids:
            return f"群/频道 {cid}"
        if run.blocked_sender_ids:
            via = getattr(msg, "via_bot_id", None)
            if via and via in run.blocked_sender_ids:
                return f"机器人 {via}"
            sender_id = getattr(getattr(msg, "from_id", None), "user_id", None)
            if sender_id and sender_id in run.blocked_sender_ids:
                return f"用户 {sender_id}"
        return None

    async def _decode_emoji_expr(self, msg, buttons=None) -> str | None:
        """只解码表达式行（💡 之后）的 Custom Emoji，忽略装饰 emoji。
        buttons 用于推断答案类型（数字/字母），传给 AI 提升识别准确率。"""
        raw = getattr(msg, "raw_text", None) or ""

        # 定位表达式行：💡 之后的最后一行
        marker = raw.rfind("💡")
        if marker < 0:
            return None
        expr_area = raw[marker:]

        # 构建 UTF-16 offset → Python 索引映射（Telegram entity 用 UTF-16）
        utf16_to_py: list[int] = []
        for i, ch in enumerate(raw):
            utf16_to_py.append(i)
            if ord(ch) > 0xFFFF:
                utf16_to_py.append(i)

        # UTF-16 起始偏移
        marker_utf16 = 0
        for ch in raw[:marker]:
            marker_utf16 += 2 if ord(ch) > 0xFFFF else 1

        # 只取表达式区域内的 custom emoji
        emoji_entities = extract_custom_emoji_ids(msg)
        expr_entities = [(o, l, d) for o, l, d in emoji_entities if o >= marker_utf16]
        if not expr_entities:
            return None

        doc_ids = list({d for _, _, d in expr_entities})
        # 用按钮类型推断期望字符类型（数字/字母），约束 AI 输出
        want_digit = None
        if buttons:
            from .claimers.captcha import _buttons_are_digit
            want_digit = _buttons_are_digit([l for l, _ in buttons])
        captcha_vc = self.run.module_configs.get("captcha") or {}
        mapping = await decode_custom_emoji(self.client, doc_ids, want_digit,
                                            vision_config=captcha_vc if captcha_vc.get("vision_api_key") else None)
        if not mapping:
            log.debug("表达式 Custom Emoji 解码失败（无映射）")
            return None

        # 保存 doc_id→char 映射供纠错用
        self._last_emoji_mapping = mapping.copy()

        # 替换
        result = list(raw)
        for offset, length, doc_id in sorted(expr_entities, key=lambda x: x[0], reverse=True):
            ch = mapping.get(doc_id)
            if not ch:
                continue
            py_start = utf16_to_py[offset] if offset < len(utf16_to_py) else len(raw)
            py_end_off = offset + length
            py_end = utf16_to_py[py_end_off] if py_end_off < len(utf16_to_py) else len(raw)
            result[py_start:py_end] = list(ch)

        decoded = "".join(result)
        log.info(f"🔤 表达式解码: {expr_area.strip()!r} → {decoded[marker:].strip()!r}")
        return decoded

    async def _on_message(self, event):
        try:
            await self._handle_message(event)
        except Exception as e:
            log.error(f"消息处理异常（不影响监听）: {e}")

    async def _handle_message(self, event):
        if not self.run.monitor_enabled:
            return

        msg = event.message

        # 屏蔽规则：私信/群/频道/用户/机器人 命中即忽略（不检测/不领取/不通知/不广播）
        block_reason = self._blocked(event, msg)
        if block_reason:
            log.debug(f"⛔ 屏蔽命中（{block_reason}），忽略消息")
            return

        # 有界去重：超阈值清空本地集合（Redis TTL 去重为主，本地仅兜底）
        if len(self._done) > _DEDUP_CAP:
            self._done.clear()
        if len(self._attempts) > _DEDUP_CAP:
            self._attempts.clear()

        text = getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""

        rp = classify(msg, self.run.direct_keywords)

        chat = event.chat
        chat_title = (getattr(chat, "title", None)
                      or getattr(chat, "first_name", None) or "私聊")

        # 未解锁红包：只广播通知，不分发领取（等解锁后的编辑事件再领）
        if (rp and rp.kind == "locked") or (
                not rp and is_red_packet(text) and getattr(msg, "reply_markup", None)):
            if self._chat_allowed(msg.chat_id):
                await self._broadcast_locked(msg, rp, chat_title)
            return

        if not rp:
            return

        if not self._chat_allowed(msg.chat_id):
            return
        if not self.run.modules.get(rp.kind, True):
            return

        # 早期去重
        from . import cache
        early_key = f"rp:detect:{msg.chat_id}:{msg.id}"
        try:
            first = await cache.setnx(early_key, "1", ex=300)
        except Exception:
            first = True
        if not first:
            return

        # 领取策略过滤（关键词/币种/金额/条件）：与账号无关，检测侧一次判定，
        # 命中即整体跳过——不分发、不广播、不消耗领取次数
        skip = check_filters(rp, text, self.run)
        if skip:
            log.info(f"⏭️ 跳过红包({_KIND_LABEL.get(rp.kind, rp.kind)}) [{chat_title}]：{skip}")
            return

        log.info(f"🧧 捕获红包({_KIND_LABEL.get(rp.kind, rp.kind)}) [{chat_title}]"
                 + ("（含表情验证码，解码中…）" if rp.needs_emoji_decode else ""))

        if rp.needs_emoji_decode:
            decoded = await self._decode_emoji_expr(msg, rp.captcha_buttons)
            if decoded:
                rp2 = classify(msg, self.run.direct_keywords, decoded_text=decoded)
                if rp2 and rp2.captcha_expr:
                    rp = rp2
                else:
                    log.warning("Custom Emoji 解码后仍无法解析")
                    return
            else:
                log.warning("Custom Emoji 解码失败")
                return

        if self.on_redpacket:
            await self.on_redpacket(msg.chat_id, msg.id, rp, chat_title, msg)
        else:
            await self.claim_remote(msg.chat_id, msg.id, rp, chat_title, msg)

    async def _broadcast_locked(self, msg, rp, chat_title):
        """未解锁红包 → 频道结构化广播（去重，只通知不领取）。"""
        from . import cache
        lock_key = f"rp:locked:{msg.chat_id}:{msg.id}"
        try:
            first = await cache.setnx(lock_key, "1", ex=600)
        except Exception:
            first = True
        if not first:
            return
        # rp 可能为 None（未知结构）→ 现场解析出锁定红包上下文
        if rp is None:
            from .detector import (RedPacket, detect_wallet, parse_conditions,
                                   parse_metadata)
            meta = parse_metadata(msg)
            rp = RedPacket("locked", state="locked", wallet=detect_wallet(msg),
                           conditions=parse_conditions(msg),
                           amount_text=meta["amount_text"], remaining=meta["remaining"],
                           sender_name=meta["sender_name"])
        # 领取策略过滤：被过滤的红包连「未解锁」广播也不发，避免刷屏
        skip = check_filters(rp, getattr(msg, "raw_text", None) or "", self.run)
        if skip:
            log.info(f"⏭️ 忽略未解锁红包 [{chat_title}]：{skip}")
            return
        log.info(f"🔒 检测到未解锁红包 [{chat_title}]")
        from .config import config as app_config
        from .notifier import broadcast_channel
        from .reporter import build_locked_report
        text = build_locked_report(rp, chat_title)
        bc_buttons = [{"text": f"直达「{chat_title}」", "url": self._msg_link(msg)}]
        asyncio.create_task(broadcast_channel(
            app_config.notify_bot_token, app_config.broadcast_channel, text, bc_buttons))

    async def claim_remote(self, chat_id, msg_id, rp, chat_title, preloaded_msg=None):
        """跨账号领取。
        webapp/dm_captcha：发 /start 码给 bot（码公用，不在群里也能领）。
        fulilai：纯 HTTP。
        direct/captcha：检测账号用本地 msg 走完整流程（含 emoji 重识别）；
          其他账号走快路径，仅凭 (chat_id, msg_id, callback data) 点击，免 re-fetch 群消息。

        返回本账号的 ClaimResult（供 runner 汇总）；未参与/被去重时返回 None。
        """
        if not self.run.claim_enabled:
            return None
        key = (chat_id, msg_id)
        if key in self._done or key in self._inflight:
            return None
        if self._attempts.get(key, 0) >= self.run.max_attempts:
            return None

        # dm_captcha 快路径：不需要群消息对象，直接走 /start → 私聊解题
        if rp.kind == "dm_captcha" and rp.start_param and rp.webapp_bot:
            self._inflight.add(key)
            try:
                return await self._run_dm_captcha_direct(rp, key, chat_title)
            finally:
                self._inflight.discard(key)

        # webapp 快路径：不需要定位消息，直接走 /start
        if rp.kind == "webapp" and rp.start_param and rp.webapp_bot:
            self._inflight.add(key)
            try:
                return await self._run_webapp_direct(rp, key, chat_title)
            finally:
                self._inflight.discard(key)

        # fulilai 快路径：不需要消息对象，纯 HTTP
        if rp.kind == "fulilai" and rp.fulilai_hash:
            if not self._fulilai:
                return None
            self._inflight.add(key)
            try:
                tm = {"detect": time.time(), "msg_date": time.time()}
                self._attempts[key] = self._attempts.get(key, 0) + 1
                self.stats["detected"] += 1
                try:
                    r = await self._fulilai.claim(None, rp.fulilai_hash, tm, startapp=rp.fulilai_startapp)
                except Exception as e:
                    # httpx 超时 / RPC 错误若直接抛出，会被 runner 的 gather 静默吞掉：
                    # 无日志、无通知、无记录。这里兜成失败结果，保证链路有反馈。
                    log.warning(f"福利来领取异常 [{chat_title}]: {e}")
                    tm["feedback"] = f"领取异常: {e}"
                    r = ClaimResult(False, retryable=False)
                tm["done"] = time.time()
                if r.ok:
                    self._done.add(key)
                    self.stats["success"] += 1
                elif not r.retryable:
                    self._done.add(key)
                else:
                    self.stats["failed"] += 1
                r.error_reason = r.error_reason or derive_error_reason(tm.get("feedback"))
                await self._report_simple(chat_title, rp, r, tm)
                return r
            finally:
                self._inflight.discard(key)

        # direct / captcha 快路径：非检测账号（无本地 msg）仅凭 callback data 点击
        if rp.kind in ("direct", "captcha") and preloaded_msg is None:
            self._inflight.add(key)
            try:
                return await self._run_callback_fast(rp, key, chat_id, msg_id, chat_title)
            finally:
                self._inflight.discard(key)

        # 检测账号：用本地 msg 走完整流程（含验证码 emoji 重识别）
        msg = preloaded_msg
        if msg is None:
            try:
                msg = await self.client.get_messages(chat_id, ids=msg_id)
            except Exception as e:
                log.debug(f"定位消息失败 [{chat_title}]: {e}")
                return None
        if not msg:
            return None

        if time.time() < self._chat_blocked.get(chat_id, 0):
            return None

        self._inflight.add(key)
        try:
            return await self._run(msg, msg, rp, key, chat_title)
        finally:
            self._inflight.discard(key)

    async def _run_webapp_direct(self, rp, key, chat_title):
        """webapp 快路径：不需要消息对象，直接用 start_param 发给 bot。"""
        tm = {"detect": time.time(), "msg_date": time.time()}
        self._attempts[key] = self._attempts.get(key, 0) + 1
        self.stats["detected"] += 1
        log.info(f"🧧 检测到红包({_KIND_LABEL['webapp']}) [{chat_title}]")

        if not self._webapp:
            log.debug("webapp 模块未就绪，跳过")
            return None

        r = await self._webapp.claim(None, rp.start_param, tm, bot_username=rp.webapp_bot)
        tm["done"] = time.time()

        if r.ok:
            self._done.add(key)
            self.stats["success"] += 1
        elif not r.retryable:
            self._done.add(key)
        else:
            self.stats["failed"] += 1

        r.error_reason = r.error_reason or derive_error_reason(tm.get("feedback"))
        await self._report_simple(chat_title, rp, r, tm)
        return r

    async def _run_dm_captcha_direct(self, rp, key, chat_title):
        """dm_captcha 快路径：/start → 私聊验证码 → 解题 → 领取。"""
        tm = {"detect": time.time(), "msg_date": time.time()}
        self._attempts[key] = self._attempts.get(key, 0) + 1
        self.stats["detected"] += 1

        r = await self._dm_captcha.claim(rp.webapp_bot, rp.start_param, tm)
        tm["done"] = time.time()

        if r.ok:
            self._done.add(key)
            self.stats["success"] += 1
        elif not r.retryable:
            self._done.add(key)
        else:
            self.stats["failed"] += 1

        r.error_reason = r.error_reason or derive_error_reason(tm.get("feedback"))
        await self._report_simple(chat_title, rp, r, tm)
        return r

    async def _run_callback_fast(self, rp, key, chat_id, msg_id, chat_title):
        """direct / captcha 跨账号快路径：仅凭 callback data 点击，不 re-fetch 群消息。

        非检测账号无 _last_emoji_mapping，无法重识别 emoji，故 wrong_captcha 不空转重试。"""
        if time.time() < self._chat_blocked.get(chat_id, 0):
            return None
        tm0 = {"detect": time.time(), "msg_date": time.time()}
        result = last = None
        while self._attempts.get(key, 0) < self.run.max_attempts:
            self._attempts[key] = self._attempts.get(key, 0) + 1
            n = self._attempts[key]
            if n == 1:
                self.stats["detected"] += 1
                log.info(f"🧧 检测到红包({_KIND_LABEL.get(rp.kind, rp.kind)}) [{chat_title}]")
            else:
                log.info(f"🔁 重试第 {n} 次 [{chat_title}]")

            tm = dict(tm0)
            if rp.kind == "direct":
                r = await self._direct.claim_fast(chat_id, msg_id, rp.direct_data, tm)
            else:  # captcha
                r = await self._captcha.claim_fast(chat_id, msg_id, rp.captcha_expr, rp.captcha_buttons, tm)
            tm["done"] = time.time()

            if r.ok:
                self._done.add(key)
                self.stats["success"] += 1
                result = (r, tm)
                break
            last = (r, tm)
            # 答错 emoji 验证码：本账号无法重识别，交给检测账号处理，不空转
            if tm.get("wrong_captcha"):
                break
            if not r.retryable:
                self._done.add(key)
                if r.amount is None and any(kw in (tm.get("feedback") or "") for kw in BLOCKED_KW):
                    self._chat_blocked[chat_id] = time.time() + 60
                break
            await asyncio.sleep(_RETRY_DELAY)

        if result is None:
            self.stats["failed"] += 1
            result = last
        if result:
            r, tm = result
            r.error_reason = r.error_reason or derive_error_reason(tm.get("feedback"))
            await self._report_simple(chat_title, rp, r, tm)
            return r
        return None

    async def _run(self, event, msg, rp, key, chat_title):
        # 编辑消息（如解锁后变可领）用 edit_date 算响应，否则用 msg.date
        base_ts = (msg.edit_date.timestamp() if msg.edit_date else msg.date.timestamp())
        tm0 = {"detect": time.time(), "msg_date": base_ts}

        result = last = None
        while self._attempts.get(key, 0) < self.run.max_attempts:
            self._attempts[key] = self._attempts.get(key, 0) + 1
            n = self._attempts[key]
            if n == 1:
                self.stats["detected"] += 1
                log.info(f"🧧 检测到红包({_KIND_LABEL.get(rp.kind, rp.kind)}) [{chat_title}]")
            else:
                log.info(f"🔁 重试第 {n} 次 [{chat_title}]")

            tm = dict(tm0)
            if rp.kind == "direct":
                r = await self._direct.claim(event, msg, rp.direct_data, tm)
            elif rp.kind == "captcha":
                r = await self._captcha.claim(event, msg, rp.captcha_expr, rp.captcha_buttons, tm)
            elif rp.kind == "dm_captcha":
                r = await self._dm_captcha.claim(rp.webapp_bot, rp.start_param, tm)
            elif rp.kind == "fulilai":
                if not self._fulilai:
                    log.debug("福利来模块未启用（缺少 2captcha key）")
                    break
                r = await self._fulilai.claim(msg, rp.fulilai_hash, tm, startapp=rp.fulilai_startapp)
            else:
                r = await self._webapp.claim(msg, rp.start_param, tm, bot_username=rp.webapp_bot)
            tm["done"] = time.time()

            if r.ok:
                self._done.add(key)
                self.stats["success"] += 1
                await self._apply_emoji_corrections(tm)
                result = (r, tm)
                break
            last = (r, tm)
            # 验证码答错 → emoji 识别错误，清掉旧映射，重新 AI 识别后重试
            if tm.get("wrong_captcha"):
                new_expr = await self._reidentify_emoji(msg, rp.captcha_buttons)
                if new_expr:
                    rp.captcha_expr = new_expr
                continue
            if not r.retryable:
                self._done.add(key)
                if r.amount is None and any(kw in (tm.get("feedback") or "") for kw in BLOCKED_KW):
                    self._chat_blocked[msg.chat_id] = time.time() + 60
                break
            await asyncio.sleep(_RETRY_DELAY)

        if result is None:
            self.stats["failed"] += 1
            result = last
        if not result:
            return None
        r, tm = result
        r.error_reason = r.error_reason or derive_error_reason(tm.get("feedback"))
        await self._report(msg, chat_title, rp, r, tm)
        return r

    def _extract_bot(self, msg) -> str:
        """从消息中提取红包来源 bot（via_bot 或发送者）。"""
        via = getattr(msg, "via_bot", None)
        if via:
            return f"@{via.username}" if via.username else via.first_name or str(via.id)
        sender = getattr(msg, "sender", None)
        if sender and getattr(sender, "bot", False):
            return f"@{sender.username}" if getattr(sender, "username", None) else getattr(sender, "first_name", "") or str(sender.id)
        return ""

    async def _reidentify_emoji(self, msg, buttons=None) -> str | None:
        """验证码答错后，只重新识别本题涉及的 emoji（不动整个包），返回新表达式。"""
        emoji_map = getattr(self, "_last_emoji_mapping", None)
        if not emoji_map:
            return None
        want_digit = None
        if buttons:
            from .claimers.captcha import _buttons_are_digit
            want_digit = _buttons_are_digit([l for l, _ in buttons])
        from .emoji_decoder import reidentify_docs
        captcha_vc = self.run.module_configs.get("captcha") or {}
        try:
            new_map = await reidentify_docs(self.client, list(emoji_map.keys()), want_digit,
                                            vision_config=captcha_vc if captcha_vc.get("vision_api_key") else None)
            if new_map:
                log.info(f"🔄 重新识别 {len(new_map)} 个 emoji")
        except Exception as e:
            log.debug(f"重新识别失败: {e}")
            return None
        # 用新映射重新解码表达式（decode 会命中刚更新的缓存）
        return await self._decode_emoji_expr(msg, buttons)

    async def _apply_emoji_corrections(self, tm: dict):
        """纠错成功后，按 doc_id → hash 精准修正（不按字符匹配，避免误改其他 emoji）。"""
        corrections = tm.get("corrections")  # {"原字符": "正确字符"}
        emoji_map = getattr(self, "_last_emoji_mapping", None)  # {doc_id: char}
        if not corrections or not emoji_map:
            return
        from .emoji_decoder import update_mapping
        # 从表达式结构推断哪些 doc_id 需要修正
        # corrections 是按字符级的，需要精准匹配到具体 doc
        for doc_id, orig_char in emoji_map.items():
            if orig_char in corrections:
                correct = corrections[orig_char]
                try:
                    await update_mapping(doc_id, correct)
                except Exception as e:
                    log.debug(f"emoji 映射回写失败: {e}")
                # 从 corrections 中移除已修正的（避免同字符多 doc 全改）
                del corrections[orig_char]
                if not corrections:
                    break

    @staticmethod
    def _msg_link(msg) -> str:
        """构造消息链接 https://t.me/c/{chat_id}/{msg_id}。"""
        cid = msg.chat_id
        if cid and cid < 0:
            cid_str = str(cid).replace("-100", "")
        else:
            cid_str = str(cid or 0)
        return f"https://t.me/c/{cid_str}/{msg.id}"

    async def _report_simple(self, chat_title, rp, r, tm):
        """无消息对象的报告（webapp 快路径用）。"""
        report = build_report(rp, r.ok, r.amount, r.winner, tm, chat_title)
        log.info(report.replace("\n", " ｜ "))
        account_name = self.me.first_name if self.me else ""
        record = {
            "ts": time.time(), "chat": chat_title,
            "target_bot": f"@{rp.webapp_bot}" if rp.webapp_bot else "",
            "account_name": account_name, "kind": rp.kind,
            "wallet": getattr(rp, "wallet", None),
            "conditions": ",".join(getattr(rp, "conditions", []) or []) or None,
            "ok": r.ok, "amount": r.amount, "winner": r.winner,
            "total_s": round(tm.get("done", 0) - tm.get("detect", 0), 2),
            "report": report,
        }
        self.history.appendleft(record)
        if self.on_record:
            try:
                await self.on_record(record)
            except Exception:
                pass
        if not await self.notifier.send(report):
            log.debug("通知未发出")

    async def _report(self, msg, chat_title, rp, r, tm):
        report = build_report(rp, r.ok, r.amount, r.winner, tm, chat_title)
        log.info(report.replace("\n", " ｜ "))
        link = self._msg_link(msg)
        target_bot = self._extract_bot(msg)
        account_name = self.me.first_name if self.me else ""
        record = {
            "ts": time.time(),
            "chat": chat_title,
            "target_bot": target_bot,
            "account_name": account_name,
            "kind": rp.kind,
            "wallet": getattr(rp, "wallet", None),
            "conditions": ",".join(getattr(rp, "conditions", []) or []) or None,
            "ok": r.ok,
            "amount": r.amount,
            "winner": r.winner,
            "total_s": round(tm.get("done", 0) - tm.get("detect", 0), 2),
            "report": report,
            "link": link,
        }
        self.history.appendleft(record)
        if self.on_record:
            try:
                await self.on_record(record)
            except Exception as e:
                log.debug(f"记录持久化失败: {e}")
        if not await self.notifier.send(report):
            log.debug("通知未发出")
