"""Account ORM model (accounts table).

[契约变更 2026-07-13 S10] 字段重整：
- nickname → remark（账号备注，NOT NULL，用户裁决）
- 新增 platform_user_id / platform_nickname（平台真实身份，自动提取不可手改）
- 删 phone（无引用）+ profile_dir（用 profile_dir_for 动态算，不入库）
- 新增 UNIQUE(platform, platform_user_id)（用户裁决：不允许重复）
  空值不参与唯一约束（SQLite 多 NULL 允许并存，未登录空壳可并存）
- 保留 color_scheme/timezone/locale/viewport_* 指纹列（spec §4.1+§7.2 一账号一固定指纹）

spec §4.1 + §7.2 revisions: adds color_scheme, timezone, locale.
UA is NOT stored here (read from real Chrome at runtime, §5.3).
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint

from semilabs_hone.core.models.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, default="xiaohongshu")
    remark = Column(String(100), nullable=False)  # 账号备注（原 nickname，改名+NOT NULL）
    platform_user_id = Column(String(64), nullable=True)  # 平台真实用户ID（登录/cookie 验证时提取）
    platform_nickname = Column(String(100), nullable=True)  # 平台真实昵称（自动提取，不可手改）
    login_method = Column(String(20), nullable=False, default="qrcode")
    viewport_w = Column(Integer, nullable=False, default=1920)
    viewport_h = Column(Integer, nullable=False, default=1080)
    color_scheme = Column(String(10), nullable=False, default="light")
    timezone = Column(String(40), nullable=False, default="Asia/Shanghai")
    locale = Column(String(20), nullable=False, default="zh-CN")
    status = Column(String(20), nullable=False, default="inactive")  # inactive/active/suspended/banned
    last_login_at = Column(DateTime, nullable=True)
    last_scrape_at = Column(DateTime, nullable=True)
    daily_scrape_count = Column(Integer, nullable=False, default=0)
    total_scrape_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)  # 连续失败计数，成功清零，达 5 → suspended
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uix_platform_user_id"),
    )

    def __repr__(self):
        return f"<Account id={self.id} platform={self.platform} remark={self.remark}>"
