"""总览聚合：账号分布 + 今日战绩 + 近 7 日趋势 + 钱包/账号分布 + token 池 + 最近成功。

供前端 Dashboard 一次拉取渲染。趋势/今日按北京时区（Asia/Shanghai）分日。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Account, GrabRecord
from ..runner import runner

router = APIRouter(prefix="/api/overview", tags=["overview"])

_TZ_NAME = "Asia/Shanghai"
_TZ = timezone(timedelta(hours=8))


@router.get("")
async def overview(s: AsyncSession = Depends(get_session)):
    # ── 账号分布 ──
    accs = (await s.execute(select(Account))).scalars().all()
    by_status: dict[str, int] = {}
    running = connected = 0
    for a in accs:
        by_status[a.status] = by_status.get(a.status, 0) + 1
        if runner.is_running(a.id):
            running += 1
        if runner.is_connected(a.id):
            connected += 1

    # ── 近 7 日趋势（按北京时区分日，一次聚合）──
    day = func.date(func.timezone(_TZ_NAME, GrabRecord.created_at))
    now_bj = datetime.now(_TZ)
    since = (now_bj - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (await s.execute(
        select(day.label("d"),
               func.count(GrabRecord.id),
               func.count(GrabRecord.id).filter(GrabRecord.ok.is_(True)))
        .where(GrabRecord.created_at >= since)
        .group_by(day))).all()
    daily = {str(r[0]): (r[1], r[2]) for r in rows}
    today_str = now_bj.strftime("%Y-%m-%d")
    trend = []
    today_total = today_success = 0
    for i in range(6, -1, -1):
        d = (now_bj - timedelta(days=i)).strftime("%Y-%m-%d")
        t, ok = daily.get(d, (0, 0))
        trend.append({"date": d[5:], "total": t, "success": ok})
        if d == today_str:
            today_total, today_success = t, ok

    # ── 累计总数/成功 ──
    row = (await s.execute(select(
        func.count(GrabRecord.id),
        func.count(GrabRecord.id).filter(GrabRecord.ok.is_(True))))).one()
    total, success = row[0] or 0, row[1] or 0

    # ── 钱包/账号分布（仅成功）──
    by_wallet = (await s.execute(
        select(GrabRecord.wallet, func.count(GrabRecord.id))
        .where(GrabRecord.ok.is_(True), GrabRecord.wallet.isnot(None))
        .group_by(GrabRecord.wallet)
        .order_by(desc(func.count(GrabRecord.id))))).all()
    by_account = (await s.execute(
        select(GrabRecord.account_name, func.count(GrabRecord.id))
        .where(GrabRecord.ok.is_(True))
        .group_by(GrabRecord.account_name)
        .order_by(desc(func.count(GrabRecord.id))).limit(8))).all()

    # ── 最近成功 ──
    recent = (await s.execute(
        select(GrabRecord).where(GrabRecord.ok.is_(True))
        .order_by(desc(GrabRecord.created_at)).limit(8))).scalars().all()

    from core.claimers.fulilai import pool_status

    return {
        "accounts": {
            "total": len(accs), "running": running, "connected": connected,
            "by_status": by_status,
        },
        "today": {
            "total": today_total, "success": today_success,
            "failed": today_total - today_success,
            "success_rate": round(today_success / today_total * 100, 1) if today_total else 0.0,
        },
        "totals": {"total": total, "success": success, "failed": total - success},
        "trend": trend,
        "by_wallet": [{"wallet": w or "unknown", "count": c} for w, c in by_wallet],
        "by_account": [{"name": n or "未知", "count": c} for n, c in by_account],
        "recent": [{
            "id": r.id, "account_name": r.account_name or "",
            "chat": r.chat or "", "wallet": r.wallet or "",
            "kind": r.kind or "", "amount": r.amount or "",
            "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in recent],
        "pool": pool_status(),
    }
