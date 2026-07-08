"""Keyword ORM model (keywords table).

spec §4.2: UNIQUE(text, platform).
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint

from semilabs_hone.core.models.db import Base


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(String(200), nullable=False)
    platform = Column(String(20), nullable=False, default="xiaohongshu")
    use_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("text", "platform", name="uq_keyword_text_platform"),
    )

    def __repr__(self):
        return f"<Keyword id={self.id} text={self.text} platform={self.platform}>"
