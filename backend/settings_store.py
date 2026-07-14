"""配置/模块存储：缺省播种、读取、更新，并组装每账号的 RunConfig。"""
import asyncio
import json
import logging

from sqlalchemy import select

from core.config import RunConfig, _parse_models, config
from core.crypto import decrypt_session

from .db import SessionLocal
from .models import BlockRule, ModuleToggle, MonitoredGroup, Setting

log = logging.getLogger("backend.settings")

# 全局设置（系统配置页）
SETTING_KEYS = ["vision_api_key", "vision_base_url", "vision_model",
                "vision_models", "max_attempts", "notify_bot_token",
                "direct_keywords", "twocaptcha_key",
                # 领取策略过滤（core/filters.py）
                "filter_keywords", "filter_currency_mode", "filter_currencies",
                "filter_min_amounts", "filter_skip_conditions",
                # 屏蔽：私信总开关（群/频道/用户/机器人走 block_rules 表）
                "block_private"]

# 领取条件的合法类型（与 detector.CONDITION_LABELS 对齐，locked 是状态不是条件）
_VALID_CONDITIONS = {"premium", "group", "user", "turnover", "winloss"}

# 各模块独立配置（模块页弹窗编辑）
MODULE_CONFIG_FIELDS: dict[str, list[dict]] = {
    "captcha": [
        {"key": "vision_api_key", "label": "AI API Key", "type": "password",
         "hint": "Custom Emoji 识别密钥（阿里云百炼 DashScope sk- 开头）"},
        {"key": "vision_base_url", "label": "API Base URL", "type": "text",
         "hint": "OpenAI 兼容端点"},
        {"key": "vision_model", "label": "识别模型", "type": "text",
         "hint": "如 qwen3-vl-flash"},
    ],
    "webapp": [
        {"key": "vision_api_key", "label": "AI API Key", "type": "password",
         "hint": "网页验证码识别密钥（阿里云百炼 DashScope sk- 开头）"},
        {"key": "vision_base_url", "label": "API Base URL", "type": "text",
         "hint": "OpenAI 兼容端点"},
        {"key": "vision_model", "label": "主模型", "type": "text",
         "hint": "单次识别默认模型，如 qwen3-vl-flash"},
        {"key": "vision_models", "label": "并发模型列表", "type": "text",
         "hint": "多模型并发，格式 model:tag 逗号分隔。如 qwen3-vl-flash:flash,qwen3-vl-plus:plus"},
    ],
    "fulilai": [
        {"key": "twocaptcha_key", "label": "2captcha API Key", "type": "password",
         "hint": "福利来红包 hCaptcha 打码服务密钥，不填则模块不生效"},
        {"key": "pool_size", "label": "候选 Token 数量", "type": "text",
         "hint": "后台维持的 hCaptcha token 数量（默认 1，越多响应越快但越费钱）"},
        {"key": "pool_enabled", "label": "打码池开关", "type": "switch",
         "hint": "关闭后停止后台打码，节省费用"},
    ],
}

# (key, label, description, sort, enabled)
MODULE_DEFAULTS = [
    ("direct", "关键词领取", "群内 callback 按钮一步到账，无验证码（最快）", 1, True),
    ("captcha", "窗口验证码", "群内算式验证码，自动解题点击（答案跨账号共享）", 2, True),
    ("dm_captcha", "私信验证码", "群内跳转 bot 私聊解算式验证码（wlqb 等）", 3, True),
    ("webapp", "网页验证", "URL 网页图片验证码，AI 多模型并发识别（okpay/kkpay）", 4, True),
    ("fulilai", "福利来红包", "hCaptcha token 池 + HTTP 领取（一 token 一号）", 5, True),
]


def _models_to_str(models) -> str:
    return ",".join(f"{m}:{t}" for m, t in models)


def _defaults() -> dict:
    return {
        "vision_api_key": config.vision_api_key,
        "vision_base_url": config.vision_base_url,
        "vision_model": config.vision_model,
        "vision_models": _models_to_str(config.vision_models),
        "max_attempts": str(config.max_attempts),
        "notify_bot_token": config.notify_bot_token,
        "direct_keywords": "领取",
        "twocaptcha_key": "",
        # 领取策略过滤：默认全部不启用
        "filter_keywords": "",        # 换行分隔
        "filter_currency_mode": "off",  # off|white|black
        "filter_currencies": "",      # 逗号分隔，大写
        "filter_min_amounts": "",     # JSON 对象 {"USDT":1,"*":0.5}
        "filter_skip_conditions": "",  # 逗号分隔 premium,turnover,...
        "block_private": "0",         # 屏蔽所有私信红包（"1" 开）
    }


# 屏蔽规则：群/频道 → 按 chat_id 拦；用户/机器人 → 按发送者/via_bot id 拦
_BLOCK_CHAT_TYPES = {"group", "channel"}
_BLOCK_SENDER_TYPES = {"user", "bot"}


async def load_blocklist(session) -> tuple[set[int], set[int]]:
    """读取屏蔽规则 → (blocked_chat_ids, blocked_sender_ids)。"""
    rows = (await session.execute(select(BlockRule))).scalars().all()
    chats = {r.target_id for r in rows if r.target_type in _BLOCK_CHAT_TYPES}
    senders = {r.target_id for r in rows if r.target_type in _BLOCK_SENDER_TYPES}
    return chats, senders


def parse_filter_settings(s: dict) -> dict:
    """把 DB 字符串形态的过滤配置解析成 RunConfig 字段（list/set/dict）。
    settings 路由热推送与 build_runconfig 共用，保证两处口径一致；坏数据一律忽略。"""
    keywords = [k.strip() for k in (s.get("filter_keywords") or "")
                .replace("\r", "").split("\n") if k.strip()]

    mode = (s.get("filter_currency_mode") or "off").strip()
    if mode not in ("off", "white", "black"):
        mode = "off"

    currencies = {c.strip().upper() for c in (s.get("filter_currencies") or "").split(",")
                  if c.strip()}

    mins: dict[str, float] = {}
    raw_mins = (s.get("filter_min_amounts") or "").strip()
    if raw_mins:
        try:
            obj = json.loads(raw_mins)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    cur = str(k).strip().upper()
                    if not cur:
                        continue
                    try:
                        mins[cur] = float(v)
                    except (TypeError, ValueError):
                        continue
        except (ValueError, TypeError):
            log.warning("filter_min_amounts 不是合法 JSON，已忽略: %r", raw_mins)

    skip_conds = {c.strip() for c in (s.get("filter_skip_conditions") or "").split(",")
                  if c.strip()} & _VALID_CONDITIONS

    return {
        "filter_keywords": keywords,
        "filter_currency_mode": mode,
        "filter_currencies": currencies,
        "filter_min_amounts": mins,
        "filter_skip_conditions": skip_conds,
    }


async def seed_defaults(session):
    """首次启动把 .env 当前值播种到 DB（已存在的设置不覆盖）。
    模块的 label/description/sort 始终对齐 MODULE_DEFAULTS（仅 enabled 保留用户选择）。"""
    existing = set((await session.execute(select(Setting.key))).scalars().all())
    for k, v in _defaults().items():
        if k not in existing:
            session.add(Setting(key=k, value=v or ""))
    ex_mod = {m.key: m for m in (await session.execute(select(ModuleToggle))).scalars().all()}
    for key, label, desc, sort, enabled in MODULE_DEFAULTS:
        row = ex_mod.get(key)
        if row is None:
            session.add(ModuleToggle(key=key, label=label, description=desc,
                                     sort=sort, enabled=enabled))
        else:
            # 名称/描述/排序对齐最新定义，enabled 保留用户开关状态
            row.label = label
            row.description = desc
            row.sort = sort
    await session.commit()
    await _migrate_to_module_configs(session)


async def _migrate_to_module_configs(session):
    """一次性迁移：把全局视觉/密钥配置复制到各模块独立配置（已存在的不覆盖）。"""
    existing = set((await session.execute(select(Setting.key))).scalars().all())
    gs = await get_settings(session)
    migrations = {
        "mod.webapp.vision_api_key": gs.get("vision_api_key", ""),
        "mod.webapp.vision_base_url": gs.get("vision_base_url", ""),
        "mod.webapp.vision_model": gs.get("vision_model", ""),
        "mod.webapp.vision_models": gs.get("vision_models", ""),
        "mod.captcha.vision_api_key": gs.get("vision_api_key", ""),
        "mod.captcha.vision_base_url": gs.get("vision_base_url", ""),
        "mod.captcha.vision_model": gs.get("vision_model", ""),
        "mod.fulilai.twocaptcha_key": gs.get("twocaptcha_key", ""),
    }
    added = False
    for k, v in migrations.items():
        if k not in existing and v:
            session.add(Setting(key=k, value=v))
            added = True
    if added:
        await session.commit()


async def get_settings(session) -> dict:
    rows = (await session.execute(select(Setting))).scalars().all()
    d = {r.key: r.value for r in rows}
    for k, v in _defaults().items():
        d.setdefault(k, v or "")
    return d


async def update_settings(session, data: dict):
    rows = {r.key: r for r in (await session.execute(select(Setting))).scalars().all()}
    for k, v in data.items():
        if k not in SETTING_KEYS:
            continue
        val = "" if v is None else str(v)
        if k in rows:
            rows[k].value = val
        else:
            session.add(Setting(key=k, value=val))
    await session.commit()


async def get_modules(session):
    return (await session.execute(
        select(ModuleToggle).order_by(ModuleToggle.sort))).scalars().all()


async def update_module(session, key: str, enabled: bool):
    m = await session.get(ModuleToggle, key)
    if m:
        m.enabled = enabled
        await session.commit()
    return m


async def modules_dict(session) -> dict:
    return {m.key: m.enabled for m in await get_modules(session)}


async def get_module_config(session, module_key: str) -> list[dict]:
    """返回模块配置字段列表（含当前值），供前端渲染表单。"""
    fields = MODULE_CONFIG_FIELDS.get(module_key)
    if not fields:
        return []
    db_keys = [f"mod.{module_key}.{f['key']}" for f in fields]
    rows = (await session.execute(
        select(Setting).where(Setting.key.in_(db_keys)))).scalars().all()
    db_map = {r.key: r.value for r in rows}
    return [{**f, "value": db_map.get(f"mod.{module_key}.{f['key']}", "")} for f in fields]


async def get_module_config_values(session, module_key: str) -> dict:
    """返回模块配置 key→value 字典（用于 build_runconfig）。"""
    fields = MODULE_CONFIG_FIELDS.get(module_key)
    if not fields:
        return {}
    db_keys = [f"mod.{module_key}.{f['key']}" for f in fields]
    rows = (await session.execute(
        select(Setting).where(Setting.key.in_(db_keys)))).scalars().all()
    db_map = {r.key: r.value for r in rows}
    return {f["key"]: db_map.get(f"mod.{module_key}.{f['key']}", "") for f in fields}


async def update_module_config(session, module_key: str, data: dict):
    """更新模块配置。"""
    fields = MODULE_CONFIG_FIELDS.get(module_key)
    if not fields:
        return
    valid_keys = {f["key"] for f in fields}
    existing = {r.key: r for r in (await session.execute(
        select(Setting).where(Setting.key.like(f"mod.{module_key}.%"))
    )).scalars().all()}
    for k, v in data.items():
        if k not in valid_keys:
            continue
        db_key = f"mod.{module_key}.{k}"
        val = "" if v is None else str(v)
        if db_key in existing:
            existing[db_key].value = val
        else:
            session.add(Setting(key=db_key, value=val))
    await session.commit()


def _module_cfg_from_settings(s: dict, module_key: str) -> dict:
    """从已拉取的全量 settings dict 中解析模块独立配置（mod.{key}.{field}），
    避免为每个模块再单独查一次 DB（原先 3 次冗余跨境往返）。"""
    fields = MODULE_CONFIG_FIELDS.get(module_key)
    if not fields:
        return {}
    return {f["key"]: s.get(f"mod.{module_key}.{f['key']}", "") for f in fields}


async def build_runconfig(session, account) -> RunConfig:
    """为单个账号组装有效运行配置（DB 设置 + 模块 + 账号 StringSession）。

    DB 往返优化：原先 6 次顺序查询 → 现 2 次。
    ① get_settings 一次拉全量 settings（已含 mod.* 键，模块配置直接解析，省 3 次）；
    ② modules_dict 与 disabled groups 用独立 session 并行（gather）。"""
    s = await get_settings(session)

    # 模块独立配置：从 settings dict 解析，零额外查询
    webapp_cfg = _module_cfg_from_settings(s, "webapp")
    captcha_cfg = _module_cfg_from_settings(s, "captcha")
    fulilai_cfg = _module_cfg_from_settings(s, "fulilai")

    # 模块开关 + 关闭秒包的群：彼此独立 → 并行查询（各自独立 session，避免同 session 并发）
    async def _load_mods() -> dict:
        async with SessionLocal() as ms:
            return await modules_dict(ms)

    async def _load_disabled() -> set:
        async with SessionLocal() as ds:
            rows = (await ds.execute(
                select(MonitoredGroup.chat_id).where(
                    MonitoredGroup.enabled.is_(False)))).scalars().all()
            return {cid for cid in rows if cid}

    async def _load_block() -> tuple[set, set]:
        async with SessionLocal() as bs:
            return await load_blocklist(bs)

    mods, disabled_ids, block = await asyncio.gather(
        _load_mods(), _load_disabled(), _load_block())
    blocked_chats, blocked_senders = block

    # webapp 视觉配置（模块级 → 全局 → .env）
    vision_key = (webapp_cfg.get("vision_api_key")
                  or s.get("vision_api_key", "") or config.vision_api_key)
    vision_base = (webapp_cfg.get("vision_base_url")
                   or s.get("vision_base_url", "") or config.vision_base_url)
    vision_model = (webapp_cfg.get("vision_model")
                    or s.get("vision_model", "") or config.vision_model)
    vision_models_raw = webapp_cfg.get("vision_models") or s.get("vision_models", "")

    # fulilai 2captcha key（模块级 → 全局）
    twocaptcha = fulilai_cfg.get("twocaptcha_key") or s.get("twocaptcha_key", "")

    try:
        max_att = int(s.get("max_attempts") or config.max_attempts)
    except (TypeError, ValueError):
        max_att = config.max_attempts
    raw_kw = s.get("direct_keywords", "领取")
    direct_kw = [k.strip() for k in raw_kw.split(",") if k.strip()]
    # disabled_ids 已在上方与 modules 并行取得
    return RunConfig(
        api_id=config.api_id,
        api_hash=config.api_hash,
        session=decrypt_session(account.session_string),
        vision_api_key=vision_key,
        vision_base_url=vision_base,
        vision_model=vision_model,
        vision_models=_parse_models(vision_models_raw),
        max_attempts=max_att,
        notify_bot_token=s.get("notify_bot_token", ""),
        chrome_path=config.chrome_path,
        modules={
            "direct": mods.get("direct", True),
            "captcha": mods.get("captcha", True),
            "dm_captcha": mods.get("dm_captcha", True),
            "webapp": mods.get("webapp", True),
            "fulilai": mods.get("fulilai", True),
        },
        twocaptcha_key=twocaptcha,
        direct_keywords=direct_kw,
        disabled_chat_ids=disabled_ids,
        monitor_enabled=getattr(account, "monitor_enabled", True),
        claim_enabled=getattr(account, "claim_enabled", True),
        module_configs={
            "captcha": captcha_cfg,
            "webapp": webapp_cfg,
            "fulilai": fulilai_cfg,
        },
        proxy=getattr(account, "proxy", None) or None,
        blocked_chat_ids=blocked_chats,
        blocked_sender_ids=blocked_senders,
        block_private=(s.get("block_private") or "0") == "1",
        **parse_filter_settings(s),
    )
