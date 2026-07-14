"""红包模块开关路由：列表 / 切换 / 模块独立配置 / 模块运行状态。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..runner import runner
from ..schemas import ModuleBatchToggle, ModuleOut, ModuleUpdate
from ..settings_store import (
    MODULE_CONFIG_FIELDS,
    get_module_config,
    get_modules,
    modules_dict,
    update_module,
    update_module_config,
)

router = APIRouter(prefix="/api/modules", tags=["modules"])


@router.get("", response_model=list[ModuleOut])
async def list_modules(s: AsyncSession = Depends(get_session)):
    return await get_modules(s)


@router.put("/{key}", response_model=ModuleOut)
async def toggle_module(key: str, body: ModuleUpdate, s: AsyncSession = Depends(get_session)):
    m = await update_module(s, key, body.enabled)
    if not m:
        raise HTTPException(404, "模块不存在")
    mods = await modules_dict(s)
    runner.hot_reload_settings(modules=mods)
    return m


@router.post("/batch")
async def batch_toggle_modules(body: ModuleBatchToggle, s: AsyncSession = Depends(get_session)):
    """一键全开/全关所有红包模块（保存后热推送到运行中 grabber）。"""
    mods = await get_modules(s)
    for m in mods:
        m.enabled = body.enabled
    await s.commit()
    runner.hot_reload_settings(modules=await modules_dict(s))
    return {"ok": True, "count": len(mods), "enabled": body.enabled}


@router.get("/{key}/config")
async def read_module_config(key: str, s: AsyncSession = Depends(get_session)):
    if key not in MODULE_CONFIG_FIELDS:
        raise HTTPException(404, "该模块无独立配置")
    return await get_module_config(s, key)


@router.put("/{key}/config")
async def write_module_config(key: str, body: dict, s: AsyncSession = Depends(get_session)):
    if key not in MODULE_CONFIG_FIELDS:
        raise HTTPException(404, "该模块无独立配置")
    await update_module_config(s, key, body)
    result = await get_module_config(s, key)
    cfg_vals = {f["key"]: f["value"] for f in result}
    runner.hot_reload_module_config(key, cfg_vals)

    # 福利来：实时调整 token 池参数
    if key == "fulilai":
        await _apply_pool_config(cfg_vals)

    return result


async def _apply_pool_config(cfg: dict):
    """保存福利来配置后立即应用 token 池参数。"""
    from core.claimers.fulilai import pool_resize, pool_set_running
    try:
        pool_resize(int(cfg.get("pool_size") or "1"))
    except (TypeError, ValueError):
        pass
    enabled = cfg.get("pool_enabled", "1") != "0"
    await pool_set_running(enabled)


@router.get("/{key}/status")
async def module_status(key: str):
    """模块运行状态（福利来=token 池状态，验证码=已识别字体数）。"""
    if key == "fulilai":
        from core.claimers.fulilai import pool_status
        return pool_status()
    if key == "captcha":
        from core.emoji_decoder import _count_glyphs
        return {"glyph_count": await _count_glyphs()}
    raise HTTPException(404, "该模块无运行状态")
