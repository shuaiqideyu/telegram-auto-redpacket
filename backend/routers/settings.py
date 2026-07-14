"""系统配置路由：读取 / 更新（AI 模型、key、通知 bot、重试次数等）。
更新后自动热推送到运行中的 grabber，无需重启。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..runner import runner
from ..schemas import SettingsUpdate
from ..settings_store import get_settings, parse_filter_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 领取策略过滤相关的设置键（任一变更即整组重新解析热推送）
_FILTER_KEYS = {"filter_keywords", "filter_currency_mode", "filter_currencies",
                "filter_min_amounts", "filter_skip_conditions"}


@router.get("")
async def read_settings(s: AsyncSession = Depends(get_session)):
    return await get_settings(s)


@router.put("")
async def write_settings(body: SettingsUpdate, s: AsyncSession = Depends(get_session)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    await update_settings(s, data)
    result = await get_settings(s)

    # 热更新运行中的 grabber
    hot = {}
    if "direct_keywords" in data:
        raw = data["direct_keywords"]
        hot["direct_keywords"] = [k.strip() for k in raw.split(",") if k.strip()]
    if "max_attempts" in data:
        try:
            hot["max_attempts"] = int(data["max_attempts"])
        except (TypeError, ValueError):
            pass
    if _FILTER_KEYS & data.keys():
        # 过滤配置整组解析（以保存后的全量设置为准，与 build_runconfig 同口径）
        hot.update(parse_filter_settings(result))
    if hot:
        runner.hot_reload_settings(**hot)

    return result
