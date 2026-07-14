"""秒包记录路由：分页查询 + 统计。"""
import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal, get_session
from ..models import GrabRecord

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("")
async def list_records(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    ok_only: bool = Query(False),
    s: AsyncSession = Depends(get_session),
):
    q = select(GrabRecord)
    if ok_only:
        q = q.where(GrabRecord.ok.is_(True))
    total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await s.execute(
        q.order_by(desc(GrabRecord.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )).scalars().all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": r.id,
                "account_name": r.account_name or "",
                "chat": r.chat or "",
                "target_bot": r.target_bot or "",
                "kind": r.kind or "",
                "wallet": r.wallet or "",
                "conditions": [c for c in (r.conditions or "").split(",") if c],
                "ok": r.ok,
                "amount": r.amount or "",
                "total_s": r.total_s,
                "report": r.report or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ],
    }


@router.get("/stats")
async def record_stats(s: AsyncSession = Depends(get_session)):
    # 总数 + 成功数：单条条件聚合（1 次往返，原先 2 次）
    row = (await s.execute(
        select(
            func.count(GrabRecord.id),
            func.count(GrabRecord.id).filter(GrabRecord.ok.is_(True)),
        )
    )).one()
    total = row[0] or 0
    success = row[1] or 0

    # 三个分组聚合彼此独立 → 并行（各自独立 session，避免同 session 并发执行）
    async def _group(col, skip_null: bool):
        conds = [GrabRecord.ok.is_(True)]
        if skip_null:
            conds.append(col.isnot(None))
        async with SessionLocal() as gs:
            return (await gs.execute(
                select(col, func.count(GrabRecord.id))
                .where(*conds)
                .group_by(col)
                .order_by(desc(func.count(GrabRecord.id)))
            )).all()

    by_account, by_bot, by_wallet = await asyncio.gather(
        _group(GrabRecord.account_name, False),
        _group(GrabRecord.target_bot, True),
        _group(GrabRecord.wallet, True),
    )

    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "by_account": [{"name": name or "未知", "count": cnt} for name, cnt in by_account],
        "by_bot": [{"bot": bot or "未知", "count": cnt} for bot, cnt in by_bot],
        "by_wallet": [{"wallet": w or "未知", "count": cnt} for w, cnt in by_wallet],
    }
