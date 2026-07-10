"""CollectionComment ORM model (collection_comments table).

PRD §6.3 — canonical comment table:
    id(UUID v4 str36) PK · item_id(str36 FK→collection_items.id, ON DELETE CASCADE)
    · platform_comment_id · author_name · content_text · like_count
    · scraped_at · UNIQUE(item_id, platform_comment_id) name=uix_item_comment

[契约变更 2026-07-10] S3 原地改表过渡：上 PRD §6 新列 + UUID PK + CASCADE FK
（item_id），同时**保留**旧 Comment 字段（post_id/platform_id/content/likes/
sub_comment_count/is_author_liked/rank/published_at/raw_json/created_at）及其
旧 UNIQUE(post_id, platform_id)，供 handlers/csv_exporter 继续使用，零逻辑
改动。post_id 的旧 FK 已移除（PRD 用 item_id），仅保留裸列。旧列在 S4/S7
重写消费者时切到 PRD 列名（post_id→item_id、content→content_text、
likes→like_count、platform_id→platform_comment_id）并删除旧 UNIQUE。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


class CollectionComment(Base):
    __tablename__ = "collection_comments"

    # --- PRD §6.3 canonical columns ---
    id = Column(String(36), primary_key=True, default=_uuid4_str)
    item_id = Column(String(36), ForeignKey("collection_items.id", ondelete="CASCADE"), nullable=True)
    platform_comment_id = Column(String(100), nullable=True)
    author_name = Column(String(100), nullable=True)
    content_text = Column(Text, nullable=True)
    like_count = Column(Integer, nullable=False, default=0)
    scraped_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # PRD §6.3 upsert dedup key
        UniqueConstraint("item_id", "platform_comment_id", name="uix_item_comment"),
        # Legacy dedup key retained for handlers/csv (S4 will migrate to the one above)
        UniqueConstraint("post_id", "platform_id", name="uq_comment_post_platform_id"),
    )

    # --- Legacy columns retained for S4/S7 consumers (to be dropped later) ---
    post_id = Column(String(36), nullable=True)  # FK dropped (PRD uses item_id)
    platform_id = Column(String(100), nullable=True)  # legacy comment id (PRD: platform_comment_id)
    content = Column(Text, nullable=True)  # nullable: repository path writes content_text only
    likes = Column(Integer, nullable=False, default=0)
    sub_comment_count = Column(Integer, nullable=False, default=0)
    is_author_liked = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    published_at = Column(DateTime, nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<CollectionComment id={self.id} post_id={self.post_id}>"
