"""Post ORM model (posts table).

spec §4.5: UNIQUE(platform, platform_id) for dedup upsert.
raw_json retained for AI analysis.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)
    platform_id = Column(String(100), nullable=False)
    task_id = Column(Integer, ForeignKey("scrape_tasks.id"), nullable=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), nullable=True)
    url = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    author_id = Column(String(100), nullable=True)
    author_name = Column(String(200), nullable=True)
    content = Column(Text, nullable=True)
    post_type = Column(String(20), nullable=True)
    image_count = Column(Integer, nullable=False, default=0)
    image_urls = Column(Text, nullable=True)
    local_images = Column(Text, nullable=True)
    likes = Column(Integer, nullable=False, default=0)
    collects = Column(Integer, nullable=False, default=0)
    comments_count = Column(Integer, nullable=False, default=0)
    shares = Column(Integer, nullable=False, default=0)
    tags = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("platform", "platform_id", name="uq_post_platform_platform_id"),
    )

    def __repr__(self):
        return f"<Post id={self.id} platform={self.platform} platform_id={self.platform_id}>"
