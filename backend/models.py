"""数据库表模型。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Account(Base):
    """Telegram 账号（含 StringSession 登录态）。"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    claim_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 账号代理（socks5://user:pass@host:port），空=直连
    proxy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Telegram 头像缩略图（data:image/jpeg;base64,...），登录/启动时同步
    avatar_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    # new(未登录) / authorized(已登录) / running(监听中) / stopped / error
    status: Mapped[str] = mapped_column(String(16), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Setting(Base):
    """系统配置（key-value，如 AI 模型/key）。"""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ModuleToggle(Base):
    """红包模块开关（direct / webapp ...）。"""
    __tablename__ = "module_toggles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class EmojiMapping(Base):
    """Custom Emoji 映射缓存。

    KKPay 等会生成无限多的 emoji 包（doc_id/set_id 都不重复），但相同字符的
    缩略图字节完全一致。因此以缩略图 MD5（thumb_hash）为稳定缓存键，doc_id 仅作参考。
    """
    __tablename__ = "emoji_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    thumb_hash: Mapped[str | None] = mapped_column(String(32), index=True, unique=True)
    set_id: Mapped[int] = mapped_column(BigInteger, index=True)
    set_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, index=True)
    character: Mapped[str] = mapped_column(String(4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChatFilter(Base):
    """群组过滤规则（白名单/黑名单）。已废弃，保留兼容旧数据。"""
    __tablename__ = "chat_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    members_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(16))  # 'whitelist' | 'blacklist'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonitoredGroup(Base):
    """秒包群组：所有账号窗口汇总去重，每群独立开关（默认开启）。"""
    __tablename__ = "monitored_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    members_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), default="group")  # 'group' | 'channel'
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_b64: Mapped[str | None] = mapped_column(Text, nullable=True)  # 头像缩略图 base64（含 data URI）
    source_accounts: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 逗号分隔 account_id
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BlockRule(Base):
    """屏蔽规则：命中的红包来源直接忽略（不检测 / 不领取 / 不通知 / 不广播）。

    target_type:
      - group   : 群组 chat_id
      - channel : 频道 chat_id
      - user    : 发红包的用户 user_id
      - bot     : 红包来源 bot id（via_bot 或发送 bot）
    「屏蔽所有私信」是全局开关，单独存 settings.block_private（不在此表）。
    """
    __tablename__ = "block_rules"
    __table_args__ = (UniqueConstraint("target_type", "target_id", name="uq_block_target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)  # group|channel|user|bot
    target_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GrabRecord(Base):
    """抢包记录（供 Web 端可视化）。"""
    __tablename__ = "grab_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_bot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wallet: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 来源钱包
    conditions: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 逗号分隔条件
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    amount: Mapped[str | None] = mapped_column(String(32), nullable=True)
    winner: Mapped[str | None] = mapped_column(String(16), nullable=True)
    total_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
