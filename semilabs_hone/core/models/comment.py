"""CollectionComment ORM model (collection_comments table).

PRD §6.3 — canonical comment table:
    id(UUID v4 str36) PK · item_id(str36 FK→collection_items.id, ON DELETE CASCADE)
    · platform_comment_id · author_name · content_text · like_count
    · scraped_at · UNIQUE(item_id, platform_comment_id) name=uix_item_comment

[契约变更 2026-07-11 S7] L03 收口：删除 S3 过渡期保留的全部旧列
（post_id/platform_id/content/likes/sub_comment_count/is_author_liked/rank/
published_at/raw_json/created_at）及旧 UNIQUE(post_id,platform_id)。
``platform_comment_id`` 改回 NOT NULL（handler 总填 ``c_pid or synth_{rank}``）。
所有消费者已迁到 PRD 列（csv_exporter 读 content_text/like_count、posts 路由/
模板读 item_id/like_count/content_text、handlers 走 repository.upsert_comment）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


class CollectionComment(Base):
    __tablename__ = "collection_comments"

    id = Column(String(36), primary_key=True, default=_uuid4_str)
    item_id = Column(String(36), ForeignKey("collection_items.id", ondelete="CASCADE"), nullable=True)
    platform_comment_id = Column(String(100), nullable=False)
    author_name = Column(String(100), nullable=True)
    content_text = Column(Text, nullable=True)
    like_count = Column(Integer, nullable=False, default=0)
    scraped_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("item_id", "platform_comment_id", name="uix_item_comment"),
    )

    def __repr__(self):
        return f"<CollectionComment id={self.id} item_id={self.item_id}>"
