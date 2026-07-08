"""Account ORM model (accounts table).

spec §4.1 + §7.2 revisions: adds color_scheme, timezone, locale.
UA is NOT stored here (read from real Chrome at runtime, §5.3).
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text

from semilabs_hone.core.models.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, default="xiaohongshu")
    phone = Column(String(20), nullable=True)
    nickname = Column(String(100), nullable=True)
    login_method = Column(String(20), nullable=False, default="qrcode")
    profile_dir = Column(String(255), nullable=True)
    viewport_w = Column(Integer, nullable=False, default=1920)
    viewport_h = Column(Integer, nullable=False, default=1080)
    color_scheme = Column(String(10), nullable=False, default="light")
    timezone = Column(String(40), nullable=False, default="Asia/Shanghai")
    locale = Column(String(20), nullable=False, default="zh-CN")
    status = Column(String(20), nullable=False, default="inactive")
    last_login_at = Column(DateTime, nullable=True)
    last_scrape_at = Column(DateTime, nullable=True)
    daily_scrape_count = Column(Integer, nullable=False, default=0)
    total_scrape_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Account id={self.id} platform={self.platform} nickname={self.nickname}>"
