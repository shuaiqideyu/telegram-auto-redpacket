"""OKPay 自动抢红包 —— 服务端入口。

统一走 Web 控制台：账号登录、模块开关、AI 配置都在前端操作，session 存数据库。

    python main.py                     # 启动后端 API（HOST/PORT 见 .env）
    cd frontend && npm run dev         # 开发态前端（代理 /api 到后端）
    cd frontend && npm run build       # 生产态前端静态产物，交给 nginx/静态托管

设 AUTOSTART_ACCOUNTS=true 时，后端启动会自动恢复所有「已启用+已登录」账号的监听
（适合服务器无人值守）；否则在前端逐个启停。
"""
import uvicorn

from core.config import config

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        reload=True,
        reload_dirs=["backend", "core"],
    )
