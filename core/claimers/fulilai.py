"""模块5 福利来红包：2captcha hCaptcha token 池 + 纯 HTTP 领取。

跨账号共享：从群消息提取的 hash 公用；但 hCaptcha token 一次性绑定单次领取，
一个 token 只能用一个号，N 个账号需要 N 个 token，故 token 池大小应配成≈账号数。
init_data 由各账号 RequestWebView 各自获取（带身份签名），不可跨号复用。

流程：
1. 从消息按钮 URL 提取 hash
2. RequestWebViewRequest 获取 init_data（绑定当前账号身份）
3. token 池取 hCaptcha token（后台 2captcha 持续解题，维持 N 个备用）
4. POST /get 领取
"""
import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from telethon.tl import functions

from ..config import config
from .base import ClaimResult, extract_amount

log = logging.getLogger("core.fulilai")

HCAPTCHA_SITEKEY = "146a7320-f922-436a-948c-dfe677fef6c4"
HCAPTCHA_PAGE = "https://img1.shjyzn.com/tgbot/"
API_BASE = "https://aliyun.xg805.com/api/gate/trade/web/redpacket"
BOT_USERNAME = "fllqb"
BOT_ID_STR = "5495837487"

TOKEN_TTL = 115
POLL_INTERVAL = 5
MAX_SOLVE_WAIT = 300


# ── Token 池（单例，所有账号共享） ──

@dataclass
class _CachedToken:
    token: str
    created: float

    @property
    def valid(self) -> bool:
        return time.time() - self.created < TOKEN_TTL


class HCaptchaTokenPool:
    """后台 2captcha 解题，维持 N 个有效 token 满额循环：
    启动即并发解 N 个；被取走/过期几个，下一轮（3s）立即补几个。"""

    def __init__(self, api_key: str, pool_size: int = 1):
        self.api_key = api_key
        self.pool_size = max(1, pool_size)
        self._tokens: deque[_CachedToken] = deque()
        self._solving = 0
        self._total_solved = 0
        self._total_failed = 0
        self._running = False
        self._http: httpx.AsyncClient | None = None

    async def start(self):
        if self._running:
            return
        self._running = True
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30)
        asyncio.create_task(self._fill_loop())
        log.info(f"福利来 token 池启动（维持 {self.pool_size} 个，有效期 {TOKEN_TTL}s）")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._http:
            await self._http.aclose()
            self._http = None
        log.info("福利来 token 池已暂停")

    def get(self) -> str | None:
        while self._tokens:
            ct = self._tokens.popleft()
            if ct.valid:
                age = time.time() - ct.created
                log.info(f"取出 token（已存活 {age:.0f}s，池剩 {len(self._tokens)} 个）")
                return ct.token
        return None

    async def solve_on_demand(self) -> str | None:
        """按需打码：池关闭或为空时，临时解一个 token（不入池）。
        红包抢的是速度，不能因为池关了就放弃。"""
        if not self._http or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30)
        try:
            return await self._do_solve()
        except Exception as e:
            log.warning(f"按需打码失败: {e}")
            return None

    @property
    def status(self) -> str:
        self._purge()
        return (f"可用:{len(self._tokens)}/{self.pool_size} "
                f"解题中:{self._solving} "
                f"成功:{self._total_solved} 失败:{self._total_failed}")

    def _purge(self):
        while self._tokens and not self._tokens[0].valid:
            self._tokens.popleft()

    async def _fill_loop(self):
        """满额循环：缺多少补多少（并发提交），消耗/过期后 3s 内回到满池。"""
        while self._running:
            self._purge()
            need = self.pool_size - len(self._tokens) - self._solving
            for _ in range(need):
                asyncio.create_task(self._solve_one())
            await asyncio.sleep(3)

    async def _solve_one(self):
        self._solving += 1
        try:
            token = await self._do_solve()
            if token:
                self._tokens.append(_CachedToken(token=token, created=time.time()))
                self._total_solved += 1
                # 池循环补充属常态（TTL 115s 持续换新），状态看 Web 模块页，不刷终端
                log.debug(f"新 token 入池（{self.status}）")
            else:
                self._total_failed += 1
        except Exception as e:
            log.warning(f"解题异常: {e}")
            self._total_failed += 1
        finally:
            self._solving -= 1

    async def _do_solve(self) -> str | None:
        r = await self._http.get("https://2captcha.com/in.php", params={
            "key": self.api_key, "method": "hcaptcha",
            "sitekey": HCAPTCHA_SITEKEY, "pageurl": HCAPTCHA_PAGE, "json": 1,
        })
        resp = r.json()
        if resp.get("status") != 1:
            log.warning(f"提交打码失败: {resp.get('request', '未知错误')}")
            return None

        task_id = resp["request"]
        t0 = time.time()
        log.debug(f"已提交打码任务 #{task_id}")

        last_log = t0
        while time.time() - t0 < MAX_SOLVE_WAIT:
            await asyncio.sleep(POLL_INTERVAL)
            r = await self._http.get("https://2captcha.com/res.php", params={
                "key": self.api_key, "action": "get", "id": task_id, "json": 1,
            })
            resp = r.json()
            elapsed = time.time() - t0
            if resp.get("status") == 1:
                log.debug(f"打码成功（耗时 {elapsed:.0f}s）")
                return resp["request"]
            if "ERROR" in resp.get("request", ""):
                log.warning(f"打码失败: {resp['request']}（耗时 {elapsed:.0f}s）")
                return None
            if time.time() - last_log >= 30:
                log.debug(f"等待打码中... 已等 {elapsed:.0f}s")
                last_log = time.time()

        log.warning(f"打码超时（{MAX_SOLVE_WAIT}s 无结果）")
        return None


# 全局单例
_pool: HCaptchaTokenPool | None = None


def get_pool(api_key: str, pool_size: int = 1) -> HCaptchaTokenPool:
    global _pool
    if _pool is None:
        _pool = HCaptchaTokenPool(api_key, pool_size=pool_size)
    else:
        _pool.pool_size = max(1, pool_size)
    return _pool


def pool_status() -> dict:
    """返回 token 池运行状态（供 Web API 查询）。"""
    if _pool is None:
        return {"running": False, "available": 0, "pool_size": 0,
                "solving": 0, "total_solved": 0, "total_failed": 0}
    _pool._purge()
    return {
        "running": _pool._running,
        "available": len(_pool._tokens),
        "pool_size": _pool.pool_size,
        "solving": _pool._solving,
        "total_solved": _pool._total_solved,
        "total_failed": _pool._total_failed,
    }


async def pool_set_running(enabled: bool):
    """外部控制 token 池启停（Web 端开关用）。"""
    if _pool is None:
        return
    if enabled and not _pool._running:
        await _pool.start()
    elif not enabled and _pool._running:
        await _pool.stop()


def pool_resize(size: int):
    """动态调整池大小（立即生效，下个补充周期按新值执行）。"""
    if _pool:
        _pool.pool_size = max(1, size)


# ── 领取器 ──

class FulilaiClaimer:
    def __init__(self, client, twocaptcha_key: str, pool_size: int = 1):
        self.client = client
        self.pool = get_pool(twocaptcha_key, pool_size=pool_size)

    async def start(self):
        await self.pool.start()

    async def claim(self, msg, hash_val: str, tm: dict,
                    startapp: str | None = None) -> ClaimResult:
        tm["mode"] = "fulilai"
        me = await self.client.get_me()
        tg_id = str(me.id)

        # init_data（用完整 startapp 或退回旧格式拼接）
        start_param = startapp or f"botId={BOT_ID_STR}_page=captcha_hash={hash_val}"
        bot = await self.client.get_input_entity(BOT_USERNAME)
        wv = await self.client(functions.messages.RequestWebViewRequest(
            peer=bot, bot=bot,
            url=f"https://t.me/{BOT_USERNAME}/wallet", platform="android",
            start_param=start_param,
        ))
        frag = urlparse(wv.url).fragment
        init_data = parse_qs(frag).get("tgWebAppData", [None])[0]
        if not init_data:
            log.warning("获取 init_data 失败")
            return ClaimResult(False, retryable=False)
        tm["init_data"] = time.time()

        # 取 token（池有现成就用，没有则按需现解一个）
        captcha_token = self.pool.get()
        if not captcha_token:
            log.info(f"池无现成 token（{self.pool.status}），按需打码中...")
            captcha_token = await self.pool.solve_on_demand()
        if not captcha_token:
            log.warning(f"打码失败/超时，跳过")
            return ClaimResult(False, retryable=False)
        tm["token"] = time.time()

        # 领取
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(f"{API_BASE}/get_status", json={
                "tg_id": tg_id, "hash": hash_val, "init_data": init_data,
            })
            status_resp = r.json()
            # data 可能为 null（hash 无效/不可领），.get("data", {}) 默认值不生效，必须 or {}
            status = status_resp.get("data") or {}
            if not status.get("status"):
                reason = status_resp.get("msg") or "已领完或过期"
                log.info(f"红包不可领: {reason}")
                tm["feedback"] = f"不可领: {reason}"
                return ClaimResult(False, retryable=False)

            r = await http.post(f"{API_BASE}/get", json={
                "tg_id": tg_id, "hash": hash_val,
                "init_data": init_data, "captcha_token": captcha_token,
            })
            resp = r.json()
            tm["submit"] = time.time()

        code = resp.get("code")
        msg_text = resp.get("msg", "")
        data = resp.get("data")

        if code == 20000:
            amount = None
            if data and isinstance(data, dict):
                amount = data.get("amount") or data.get("money")
            if amount:
                amount = str(amount)
            tm["amount"] = time.time()
            log.info(f"💰 福利来领取成功{f' {amount}' if amount else ''}")
            return ClaimResult(True, amount, "fulilai", retryable=False)

        if "已领完" in msg_text or "过期" in msg_text or "已领取" in msg_text:
            log.info(f"红包已领完: {msg_text}")
            return ClaimResult(False, retryable=False)
        if "验证码" in msg_text:
            log.warning(f"验证码无效: {msg_text}")
            return ClaimResult(False, retryable=True)

        log.warning(f"领取失败: {msg_text} (code={code})")
        return ClaimResult(False, retryable=False)
