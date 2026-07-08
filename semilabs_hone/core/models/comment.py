"""Comment ORM model (comments table).

spec §4.6: UNIQUE(post_id, platform_id).
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    platform_id = Column(String(100), nullable=True)
    author_name = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    likes = Column(Integer, nullable=False, default=0)
    sub_comment_count = Column(Integer, nullable=False, default=0)
    is_author_liked = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, nullable=True)
    rank = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("post_id", "platform_id", name="uq_comment_post_platform_id"),
    )

    def __repr__(self):
        return f"<Comment id={self.id} post_id={self.post_id}>"
