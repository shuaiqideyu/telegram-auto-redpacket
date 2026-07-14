"""FastAPI 入口：CORS + 路由 + 启动建表/播种。

启动：uvicorn backend.app:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from core.config import config

from .db import SessionLocal, init_db
from .models import Account
from .routers import accounts, blocklist, groups, modules, overview, records, settings
from .runner import runner
from .settings_store import seed_defaults

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
for _n in ("telethon", "httpx", "httpcore", "openai", "asyncio"):
    logging.getLogger(_n).setLevel(logging.WARNING)


class _AccessNoiseFilter(logging.Filter):
    """过滤 uvicorn access log 噪音：200 正常请求 + 头像 404（群组无头像属预期）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        msg = record.getMessage()
        if "200" in msg:
            return False
        if "/api/groups/avatar/" in msg and "404" in msg:
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_AccessNoiseFilter())

log = logging.getLogger("backend")


async def _autostart():
    """根据 DB 中 enabled=True 的账号自动恢复监听。"""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Account).where(
                Account.enabled.is_(True),
                Account.session_string.isnot(None)))).scalars().all()
    if not rows:
        return
    started = 0
    for acc in rows:
        try:
            await runner.start(acc)
            started += 1
        except Exception as e:
            log.error(f"自动启动账号 {acc.id} 失败: {e}")
    log.info(f"🔁 自动恢复 {started} 个账号监听")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as s:
        await seed_defaults(s)
    log.info("✅ 后端就绪（DB 已建表/播种）")
    await _autostart()
    yield
    await runner.stop_all()
    from core.cache import close as close_redis
    await close_redis()


app = FastAPI(title="Telegram 自动抢红包 v0.2 学习版", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 本地内网控制台，放开跨域
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router)
app.include_router(settings.router)
app.include_router(modules.router)
app.include_router(groups.router)
app.include_router(records.router)
app.include_router(blocklist.router)
app.include_router(overview.router)


_ERR_MAP = {
    "SESSION_ENCRYPT_KEY 未配置": "服务端 Session 解密密钥未配置，请检查 .env 中的 SESSION_ENCRYPT_KEY",
    "session 失效": "账号 Session 已失效，请重新登录",
}


@app.exception_handler(Exception)
async def _global_error(request: Request, exc: Exception):
    msg = str(exc)
    for pattern, zh in _ERR_MAP.items():
        if pattern in msg:
            msg = zh
            break
    log.error("请求 %s 异常: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": msg})


@app.get("/api/health")
async def health():
    return {"ok": True}


_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="static")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str):
        file = _DIST / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(_DIST / "index.html")
