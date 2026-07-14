"""API 请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel


# ---- 账号 ----
class AccountOut(BaseModel):
    id: int
    phone: str
    user_id: int | None = None
    name: str | None = None
    username: str | None = None
    enabled: bool
    status: str
    monitor_enabled: bool = True
    claim_enabled: bool = True
    running: bool = False
    connected: bool = False          # telethon 实际连接态
    has_session: bool = False
    groups_count: int = 0
    proxy: str | None = None
    avatar_url: str | None = None    # 头像端点 URL（懒加载，404=无头像）
    uptime_s: float | None = None    # 本次运行时长（秒），未运行=None
    detected: int = 0                # 本次运行累计：检测/成功/失败
    success: int = 0
    failed: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProxyUpdate(BaseModel):
    proxy: str | None = None


class LoginStart(BaseModel):
    phone: str


class LoginCode(BaseModel):
    phone: str
    code: str


class LoginPassword(BaseModel):
    phone: str
    password: str


# ---- QR 扫码登录 ----
class QRLoginPoll(BaseModel):
    bind_id: str


class QRLoginPassword(BaseModel):
    bind_id: str
    password: str


# ---- Session 批量导入 ----
class ImportSessions(BaseModel):
    sessions: list[str]


# ---- 系统配置 ----
class SettingsUpdate(BaseModel):
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_model: str | None = None
    vision_models: str | None = None
    max_attempts: int | str | None = None
    notify_bot_token: str | None = None
    direct_keywords: str | None = None
    twocaptcha_key: str | None = None
    # 领取策略过滤（均为字符串形态，解析见 settings_store.parse_filter_settings）
    filter_keywords: str | None = None        # 换行分隔关键词黑名单
    filter_currency_mode: str | None = None   # off|white|black
    filter_currencies: str | None = None      # 逗号分隔币种
    filter_min_amounts: str | None = None     # JSON {"USDT":1,"*":0.5}
    filter_skip_conditions: str | None = None  # 逗号分隔条件类型


# ---- 秒包群组 ----
class GroupOut(BaseModel):
    id: int
    chat_id: int
    title: str | None = None
    username: str | None = None
    members_count: int | None = None
    chat_type: str = "group"       # 'group' | 'channel'
    avatar_url: str | None = None
    enabled: bool = True
    pinned: bool = False
    source_count: int = 0          # 几个账号在这个群里
    source_account_ids: list[int] = []  # 哪些账号在这个群里
    updated_at: datetime | None = None


class GroupToggle(BaseModel):
    enabled: bool


class GroupBatchToggle(BaseModel):
    enabled: bool


class GroupPin(BaseModel):
    pinned: bool


# ---- 模块开关 ----
class ModuleOut(BaseModel):
    key: str
    label: str
    enabled: bool
    description: str
    sort: int

    model_config = {"from_attributes": True}


class ModuleUpdate(BaseModel):
    enabled: bool


class ModuleBatchToggle(BaseModel):
    enabled: bool


# ---- 屏蔽规则 ----
class BlockRuleIn(BaseModel):
    target_type: str          # group | channel | user | bot
    target_id: int
    target_name: str | None = None
    note: str | None = None


class BlockRuleOut(BaseModel):
    id: int
    target_type: str
    target_id: int
    target_name: str | None = None
    note: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class BlockPrivateUpdate(BaseModel):
    enabled: bool
