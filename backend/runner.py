"""账号运行管理：按账号启动/停止 grabber（每账号一个 asyncio 任务）。

直接信任 DB 中的 session 启动 grabber，运行中出异常再标记状态。
"""
import asyncio
import logging
import time

from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)

from core import cache
from core.config import config
from core.detector import KIND_LABELS
from core.grabber import OKPayGrabber
from core.notifier import broadcast_channel
from core.reporter import build_detect_report, build_summary_report

from .db import SessionLocal
from .models import Account, GrabRecord
from .settings_store import build_runconfig

log = logging.getLogger("backend.runner")

_KIND_LABEL = KIND_LABELS  # 唯一文案来源在 detector.KIND_LABELS


class Runner:
    def __init__(self):
        self._runs: dict[int, dict] = {}  # account_id -> {grabber, task}

    def is_running(self, account_id: int) -> bool:
        r = self._runs.get(account_id)
        return bool(r and not r["task"].done())

    def groups_count(self, account_id: int) -> int:
        """该账号监听的群组/频道数（窗口数）。未运行返回 0。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            return getattr(r["grabber"], "groups_count", 0)
        return 0

    def stats(self, account_id: int) -> dict:
        """该账号本次运行累计战绩 {detected, success, failed}。未运行返回空。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            return dict(getattr(r["grabber"], "stats", None) or {})
        return {}

    def uptime(self, account_id: int) -> float | None:
        """该账号本次运行时长（秒）。未运行返回 None。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            return max(0.0, time.time() - r.get("started_at", time.time()))
        return None

    def is_connected(self, account_id: int) -> bool:
        """telethon 是否实际处于连接态（区别于 task 是否存活）。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            client = getattr(r["grabber"], "client", None)
            try:
                return bool(client and client.is_connected())
            except Exception:
                return False
        return False

    def update_flags(self, account_id: int, monitor: bool | None = None, claim: bool | None = None):
        """热更新运行中账号的监控/秒包开关（无需重启）。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            if monitor is not None:
                r["grabber"].run.monitor_enabled = monitor
            if claim is not None:
                r["grabber"].run.claim_enabled = claim

    def hot_reload_settings(self, **kwargs):
        """热更新所有运行中 grabber 的配置字段（无需重启）。
        支持: direct_keywords, max_attempts, modules 等 RunConfig 上的属性。"""
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            run = info["grabber"].run
            for k, v in kwargs.items():
                if hasattr(run, k):
                    setattr(run, k, v)
        if kwargs:
            log.info(f"热更新配置: {list(kwargs.keys())} → {len(self._runs)} 账号")

    def hot_reload_module_config(self, module_key: str, cfg: dict):
        """热更新单个模块的独立配置到所有运行中 grabber。"""
        count = 0
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            info["grabber"].run.module_configs[module_key] = cfg
            count += 1
        if count:
            log.info(f"热更新模块配置 [{module_key}] → {count} 账号")

    async def scan_all_groups(self) -> list[dict]:
        """遍历所有运行中 grabber，汇总去重所有账号的群组/频道列表。
        返回 [{chat_id, title, username, members_count, source_accounts:[aid,...]}]。"""
        merged: dict[int, dict] = {}
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            grabber = info["grabber"]
            try:
                groups = await grabber.scan_groups()
            except Exception as e:
                log.debug(f"账号 {aid} 扫描群组失败: {e}")
                continue
            for g in groups:
                cid = g["chat_id"]
                if cid not in merged:
                    merged[cid] = {
                        "chat_id": cid,
                        "title": g.get("title") or "",
                        "username": g.get("username") or "",
                        "members_count": g.get("members_count"),
                        "chat_type": g.get("chat_type") or "group",
                        "source_accounts": [aid],
                    }
                else:
                    m = merged[cid]
                    if aid not in m["source_accounts"]:
                        m["source_accounts"].append(aid)
                    # 用更完整的信息补全
                    if not m["title"] and g.get("title"):
                        m["title"] = g["title"]
                    if not m["username"] and g.get("username"):
                        m["username"] = g["username"]
                    if m["members_count"] is None and g.get("members_count") is not None:
                        m["members_count"] = g["members_count"]
                    if g.get("chat_type"):
                        m["chat_type"] = g["chat_type"]
        return list(merged.values())

    async def download_avatar(self, chat_id: int) -> bytes | None:
        """用任一运行中账号的 telethon 客户端下载群组头像缩略图（小图）。
        私有群也能取到（只要账号在群里），优于 t.me 公开接口。"""
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            client = info["grabber"].client
            try:
                data = await client.download_profile_photo(
                    chat_id, file=bytes, download_big=False)
                if data:
                    return data
            except Exception as e:
                log.debug(f"账号 {aid} 下载头像 {chat_id} 失败: {e}")
                continue
        return None

    async def download_self_avatar(self, account_id: int, user_id: int | None) -> bytes | None:
        """下载账号本人头像缩略图：优先用账号自己的 client 取 'me'，
        不在线时回退用任一在线账号按 user_id 取（需双方可见）。"""
        r = self._runs.get(account_id)
        if r and not r["task"].done():
            client = getattr(r["grabber"], "client", None)
            try:
                if client and client.is_connected():
                    data = await client.download_profile_photo(
                        "me", file=bytes, download_big=False)
                    if data:
                        return data
            except Exception as e:
                log.debug(f"账号 {account_id} 下载自身头像失败: {e}")
        if user_id:
            return await self.download_avatar(user_id)
        return None

    def hot_reload_disabled_groups(self, disabled_ids: set[int]):
        """热更新关闭秒包的群 ID 集合到所有运行中 grabber。"""
        count = 0
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            info["grabber"].run.disabled_chat_ids = set(disabled_ids)
            count += 1
        if count:
            log.info(f"热更新秒包群开关 → {count} 账号（关闭 {len(disabled_ids)} 个群）")

    def hot_reload_blocklist(self, chats: set[int], senders: set[int],
                             block_private: bool | None = None):
        """热更新屏蔽规则（群/频道 + 用户/机器人 + 私信开关）到所有运行中 grabber。"""
        count = 0
        for aid, info in self._runs.items():
            if info["task"].done():
                continue
            run = info["grabber"].run
            run.blocked_chat_ids = set(chats)
            run.blocked_sender_ids = set(senders)
            if block_private is not None:
                run.block_private = block_private
            count += 1
        if count:
            log.info(f"热更新屏蔽规则 → {count} 账号"
                     f"（群/频道 {len(chats)}，用户/机器人 {len(senders)}）")

    async def start(self, account: Account):
        if self.is_running(account.id):
            return
        async with SessionLocal() as s:
            run = await build_runconfig(s, account)
        aid = account.id

        async def on_record(rec: dict):
            async with SessionLocal() as s:
                s.add(GrabRecord(
                    account_id=aid,
                    account_name=rec.get("account_name"),
                    chat=rec.get("chat"),
                    target_bot=rec.get("target_bot"),
                    kind=rec.get("kind"),
                    wallet=rec.get("wallet"),
                    conditions=rec.get("conditions"),
                    ok=rec.get("ok", False),
                    amount=rec.get("amount"),
                    winner=rec.get("winner"),
                    total_s=rec.get("total_s"),
                    report=rec.get("report")))
                await s.commit()

        async def on_ready(g: OKPayGrabber):
            await self._sync_name(aid, g)

        async def on_redpacket(chat_id, msg_id, rp, chat_title, msg):
            await self._on_redpacket(aid, chat_id, msg_id, rp, chat_title, msg)

        grabber = OKPayGrabber(
            run=run, on_record=on_record, on_ready=on_ready,
            on_redpacket=on_redpacket)
        task = asyncio.create_task(self._run(aid, grabber))
        self._runs[aid] = {"grabber": grabber, "task": task, "started_at": time.time()}
        await self._set_status(aid, "running")
        log.info(f"账号 {aid} 已启动监听")

    async def _on_redpacket(self, detector_aid, chat_id, msg_id, rp, chat_title, msg):
        """红包统一调度：Redis 去重 → 广播通信频道 → 分发所有账号并发领取。"""
        # Redis 去重：同一红包+同种分类 只处理一次（key 含 kind，消息编辑切换类型后可重新触发）。
        # TTL 必须覆盖红包整个生命周期（每次被领取都触发编辑事件）：曾用 300s，
        # 过期后旧红包的编辑事件会被当新红包重复调度（叠加重启丢内存 _done），故拉长到 24h。
        dedup_key = f"rp:{chat_id}:{msg_id}:{rp.kind}"
        try:
            first = await cache.setnx(dedup_key, "1", ex=86400)
        except Exception as e:
            log.debug(f"Redis 去重失败，降级本地处理: {e}")
            first = True
        if not first:
            return

        # 只分发给开启了「秒包」的账号
        grabbers = [(aid, info["grabber"]) for aid, info in self._runs.items()
                    if not info["task"].done()
                    and getattr(info["grabber"].run, "claim_enabled", True)]
        if not grabbers:
            return

        kind_label = _KIND_LABEL.get(rp.kind, rp.kind)
        log.info(f"🧧 红包调度 [{chat_title}] {kind_label} → 分发 {len(grabbers)} 账号")

        cid_str = str(chat_id).replace("-100", "") if chat_id < 0 else str(chat_id)
        link = f"https://t.me/c/{cid_str}/{msg_id}"
        bc_buttons = [{"text": f"直达「{chat_title}」", "url": link}]

        # 阶段1：检测广播（结构化：钱包/金额/条件/算式/分发数）
        asyncio.create_task(
            broadcast_channel(config.notify_bot_token, config.broadcast_channel,
                              build_detect_report(rp, chat_title, len(grabbers)),
                              bc_buttons))

        # 并发分发：detector 用自带 msg（免 re-fetch），其他账号 re-fetch
        t0 = time.monotonic()

        async def timed_claim(g: OKPayGrabber, preloaded):
            """各账号独立计时（同一起点 t0 → 本账号出结果），供汇总逐账号展示。"""
            res = await g.claim_remote(chat_id, msg_id, rp, chat_title, preloaded)
            if res is not None and getattr(res, "total_s", None) is None:
                res.total_s = round(time.monotonic() - t0, 2)
            return res

        tasks = []
        for aid, g in grabbers:
            preloaded = msg if aid == detector_aid else None
            tasks.append(timed_claim(g, preloaded))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 阶段3：领取汇总广播（仅统计真正参与并有结果的账号）
        summary: list[tuple[str, object]] = []
        for (aid, g), res in zip(grabbers, results):
            if isinstance(res, Exception):
                log.debug(f"账号 {aid} 领取异常: {res}")
                continue
            if res is None:
                continue
            name = (g.me.first_name if getattr(g, "me", None) else None) or f"账号{aid}"
            summary.append((name, res))
        if summary:
            asyncio.create_task(
                broadcast_channel(config.notify_bot_token, config.broadcast_channel,
                                  build_summary_report(rp, summary, chat_title),
                                  bc_buttons))

    async def _sync_name(self, aid: int, grabber: OKPayGrabber):
        """grabber 连接后同步昵称到 DB（非阻塞，失败不影响运行）。"""
        try:
            if grabber.me:
                async with SessionLocal() as s:
                    acc = await s.get(Account, aid)
                    if acc and acc.name != grabber.me.first_name:
                        acc.name = grabber.me.first_name
                        await s.commit()
        except Exception:
            pass

    async def _run(self, aid: int, grabber: OKPayGrabber):
        try:
            await grabber.start()
        except asyncio.CancelledError:
            pass
        except (UserDeactivatedError, UserDeactivatedBanError):
            log.error(f"账号 {aid} 已被封禁")
            await self._set_status(aid, "banned", enabled=False)
            return
        except (AuthKeyUnregisteredError, AuthKeyDuplicatedError):
            log.error(f"账号 {aid} Session 已失效")
            await self._set_status(aid, "expired", enabled=False)
            return
        except Exception as e:
            log.error(f"账号 {aid} 运行异常: {e}")
            await self._set_status(aid, "error", enabled=False)
        finally:
            if aid in self._runs:
                await self._set_status(aid, "stopped")

    async def stop(self, account_id: int):
        r = self._runs.pop(account_id, None)
        if not r:
            return
        try:
            await r["grabber"].stop()
        except Exception as e:
            log.debug(f"停止账号 {account_id} 异常: {e}")
        r["task"].cancel()
        await self._set_status(account_id, "stopped")
        log.info(f"账号 {account_id} 已停止")

    async def stop_all(self):
        for aid in list(self._runs):
            await self.stop(aid)

    async def _set_status(self, aid: int, status: str, enabled: bool | None = None):
        try:
            async with SessionLocal() as s:
                acc = await s.get(Account, aid)
                if acc:
                    acc.status = status
                    if enabled is not None:
                        acc.enabled = enabled
                    await s.commit()
        except Exception as e:
            log.debug(f"更新状态失败: {e}")


runner = Runner()
