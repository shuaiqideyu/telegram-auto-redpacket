"""福利来红包抢包器 — 2captcha token 池 + 纯 HTTP 领取。

架构：
  TokenPool:  后台持续用 2captcha 解 hCaptcha，池中维持 1 个有效 token
  claim():    红包来了 → 池取 token → POST /get → 到账

用法：
  python -m tools.fulilai_grab --msg 1066                  # 领取指定红包
  python -m tools.fulilai_grab --msg 1066 --no-pool         # 不用池，即时解题领取
  python -m tools.fulilai_grab --pool-only                  # 只启动池，观察 token 生成
  python -m tools.fulilai_grab --msg 1066 --pool-wait 300   # 池等待上限（秒）
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
for _noisy in ("httpx", "httpcore", "telethon", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("fulilai")

# ── 配置 ──

TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "")
HCAPTCHA_SITEKEY = "146a7320-f922-436a-948c-dfe677fef6c4"
HCAPTCHA_PAGE = "https://img1.shjyzn.com/tgbot/"
API_BASE = "https://aliyun.xg805.com/api/gate/trade/web/redpacket"
BOT_USERNAME = "fllqb"
BOT_ID_STR = "5495837487"
CHAT_ID = -1003807395473

TOKEN_TTL = 115      # token 有效期（秒）
POLL_INTERVAL = 5    # 2captcha 轮询间隔
MAX_SOLVE_WAIT = 300 # 单次最长等待


# ── Token 池 ──

@dataclass
class CachedToken:
    token: str
    created: float

    @property
    def valid(self) -> bool:
        return time.time() - self.created < TOKEN_TTL


class TokenPool:
    """后台持续解 hCaptcha，池中维持 pool_size 个有效 token。"""

    def __init__(self, pool_size: int = 1):
        self.pool_size = pool_size
        self._tokens: deque[CachedToken] = deque()
        self._solving = 0
        self._total_solved = 0
        self._total_failed = 0
        self._running = False
        self._http: httpx.AsyncClient | None = None

    async def start(self):
        self._running = True
        self._http = httpx.AsyncClient(timeout=30)
        log.info(f"Token 池启动（维持 {self.pool_size} 个，有效期 {TOKEN_TTL}s）")
        asyncio.create_task(self._fill_loop())

    async def stop(self):
        self._running = False
        if self._http:
            await self._http.aclose()

    def get(self) -> str | None:
        while self._tokens:
            ct = self._tokens.popleft()
            if ct.valid:
                age = time.time() - ct.created
                log.info(f"取出 token（已存活 {age:.0f}s，池剩 {len(self._tokens)} 个）")
                return ct.token
        return None

    @property
    def available(self) -> int:
        self._purge()
        return len(self._tokens)

    @property
    def status(self) -> str:
        self._purge()
        return (f"可用:{len(self._tokens)}/{self.pool_size}  "
                f"解题中:{self._solving}  "
                f"累计成功:{self._total_solved}  失败:{self._total_failed}")

    async def wait_for_token(self, timeout: float = 300) -> str | None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            token = self.get()
            if token:
                return token
            await asyncio.sleep(1)
        return None

    def _purge(self):
        while self._tokens and not self._tokens[0].valid:
            self._tokens.popleft()

    async def _fill_loop(self):
        while self._running:
            self._purge()
            need = self.pool_size - len(self._tokens) - self._solving
            if need > 0:
                for _ in range(need):
                    asyncio.create_task(self._solve_one())
            await asyncio.sleep(3)

    async def _solve_one(self):
        self._solving += 1
        try:
            token = await self._do_solve()
            if token:
                self._tokens.append(CachedToken(token=token, created=time.time()))
                self._total_solved += 1
                log.info(f"新 token 入池（{self.status}）")
            else:
                self._total_failed += 1
        except Exception as e:
            log.warning(f"解题异常: {e}")
            self._total_failed += 1
        finally:
            self._solving -= 1

    async def _do_solve(self) -> str | None:
        r = await self._http.get("https://2captcha.com/in.php", params={
            "key": TWOCAPTCHA_KEY, "method": "hcaptcha",
            "sitekey": HCAPTCHA_SITEKEY, "pageurl": HCAPTCHA_PAGE, "json": 1,
        })
        resp = r.json()
        if resp.get("status") != 1:
            log.warning(f"提交打码失败: {resp.get('request', '未知错误')}")
            return None

        task_id = resp["request"]
        t0 = time.time()
        log.info(f"已提交打码任务 #{task_id}，等待人工处理...")

        last_log = t0
        while time.time() - t0 < MAX_SOLVE_WAIT:
            await asyncio.sleep(POLL_INTERVAL)
            r = await self._http.get("https://2captcha.com/res.php", params={
                "key": TWOCAPTCHA_KEY, "action": "get", "id": task_id, "json": 1,
            })
            resp = r.json()
            elapsed = time.time() - t0
            if resp.get("status") == 1:
                log.info(f"打码成功（耗时 {elapsed:.0f}s）")
                return resp["request"]
            if "ERROR" in resp.get("request", ""):
                log.warning(f"打码失败: {resp['request']}（耗时 {elapsed:.0f}s）")
                return None
            if time.time() - last_log >= 30:
                log.info(f"等待打码中... 已等 {elapsed:.0f}s")
                last_log = time.time()

        log.warning(f"打码超时（{MAX_SOLVE_WAIT}s 无结果）")
        return None


# ── Telethon 工具 ──

async def _get_session() -> str:
    from backend.db import SessionLocal
    from backend.models import Account
    from core.crypto import decrypt_session
    from sqlalchemy import select
    async with SessionLocal() as sess:
        row = (await sess.execute(
            select(Account).where(Account.session_string.isnot(None)).limit(1)
        )).scalar_one_or_none()
        if row and row.session_string:
            return decrypt_session(row.session_string)
    raise RuntimeError("数据库无已登录账号")


def _extract_hash(msg) -> str | None:
    from telethon.tl.types import KeyboardButtonUrl
    if not msg or not msg.reply_markup:
        return None
    for row in msg.reply_markup.rows:
        for btn in row.buttons:
            if isinstance(btn, KeyboardButtonUrl) and "startapp=" in (btn.url or ""):
                m = re.search(r"hash=([a-f0-9]+)", btn.url)
                if m:
                    return m.group(1)
    return None


async def get_init_data(client, hash_val: str) -> str | None:
    from telethon.tl import functions
    bot = await client.get_input_entity(BOT_USERNAME)
    result = await client(functions.messages.RequestWebViewRequest(
        peer=bot, bot=bot,
        url=f"https://t.me/{BOT_USERNAME}/wallet", platform="android",
        start_param=f"botId={BOT_ID_STR}_page=captcha_hash={hash_val}",
    ))
    frag = urlparse(result.url).fragment
    return parse_qs(frag).get("tgWebAppData", [None])[0]


# ── 领取流程 ──

async def claim(tg_id: str, hash_val: str, init_data: str, captcha_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(f"{API_BASE}/get_status", json={
            "tg_id": tg_id, "hash": hash_val, "init_data": init_data,
        })
        status = r.json()
        sd = status.get("data", {})
        face = sd.get("face_check_open", False)
        avail = sd.get("status", False)
        log.info(f"红包状态: 可领={avail}  需验证={face}")

        if not avail:
            return {"code": -1, "msg": "红包不可领（已领完或过期）"}

        log.info("提交领取请求...")
        r = await http.post(f"{API_BASE}/get", json={
            "tg_id": tg_id, "hash": hash_val,
            "init_data": init_data, "captcha_token": captcha_token,
        })
        return r.json()


# ── 主流程 ──

async def run(args):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if args.pool_only:
        pool = TokenPool(pool_size=1)
        await pool.start()
        log.info("池观察模式，Ctrl+C 退出")
        try:
            while True:
                await asyncio.sleep(10)
                log.info(f"[池状态] {pool.status}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await pool.stop()
        return

    session_str = await _get_session()
    client = TelegramClient(StringSession(session_str), config.api_id, config.api_hash)
    await client.connect()
    me = await client.get_me()
    tg_id = str(me.id)
    log.info(f"已登录: {me.first_name}（{tg_id}）")

    msg = await client.get_messages(CHAT_ID, ids=args.msg)
    hash_val = _extract_hash(msg)
    if not hash_val:
        log.error("消息中未找到红包 hash")
        await client.disconnect()
        return
    log.info(f"红包 hash: {hash_val}")

    init_data = await get_init_data(client, hash_val)
    if not init_data:
        log.error("获取 init_data 失败")
        await client.disconnect()
        return
    log.info("init_data 获取成功")

    pool = TokenPool(pool_size=1)
    await pool.start()
    log.info(f"等待 token（上限 {args.pool_wait}s）...")
    captcha_token = await pool.wait_for_token(timeout=args.pool_wait)
    await pool.stop()

    if not captcha_token:
        log.error(f"等待超时，未拿到 token（{pool.status}）")
        await client.disconnect()
        return

    t0 = time.time()
    result = await claim(tg_id, hash_val, init_data, captcha_token)
    elapsed = time.time() - t0
    code = result.get("code")
    msg_text = result.get("msg", "")
    data = result.get("data")

    print(f"\n{'='*50}")
    if code == 20000:
        print(f"🎉 领取成功！（{elapsed:.1f}s）")
        if data:
            print(f"   {json.dumps(data, ensure_ascii=False)}")
    else:
        print(f"领取失败: {msg_text}（code={code}，{elapsed:.1f}s）")
    print(f"{'='*50}")

    await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="福利来红包抢包器")
    parser.add_argument("--msg", type=int, help="红包消息 ID")
    parser.add_argument("--no-pool", action="store_true", help="不用池，即时解题")
    parser.add_argument("--pool-only", action="store_true", help="只启动池观察")
    parser.add_argument("--pool-wait", type=int, default=300, help="池等待上限秒")
    args = parser.parse_args()
    if not args.msg and not args.pool_only:
        parser.error("需要 --msg 或 --pool-only")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
