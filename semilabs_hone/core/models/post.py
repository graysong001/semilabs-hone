"""CollectionItem ORM model (collection_items table).

PRD §6.2 — canonical item table:
    id(UUID v4 str36) PK · task_id(str36 FK→collection_tasks.id, ON DELETE CASCADE)
    · platform · platform_id · url · title · content_text · author_name
    · metrics_json(TEXT, default '{}') · publish_time(VARCHAR(50), 容错)
    · scraped_at · UNIQUE(platform, platform_id) name=uix_platform_item

[契约变更 2026-07-10] S3 原地改表过渡：上 PRD §6 新列 + UUID PK + CASCADE FK，
同时**保留**旧 Post 字段（content/likes/collects/comments_count/shares/tags/
post_type/image_count/image_urls/local_images/published_at/raw_json/keyword_id/
created_at），供 handlers/csv_exporter/posts 路由/tests 继续使用，零逻辑改动。
keyword_id 的旧 FK 已移除（PRD 无该列），仅保留裸列。旧列在 S4/S7 重写消费者
时切到 PRD 列名（content→content_text、likes 等并入 metrics_json）并删除。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


class CollectionItem(Base):
    __tablename__ = "collection_items"

    # --- PRD §6.2 canonical columns ---
    id = Column(String(36), primary_key=True, default=_uuid4_str)
    task_id = Column(String(36), ForeignKey("collection_tasks.id", ondelete="CASCADE"), nullable=True)
    platform = Column(String(20), nullable=False)
    platform_id = Column(String(100), nullable=False)
    url = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    author_name = Column(String(100), nullable=True)
    metrics_json = Column(Text, nullable=False, default="{}", server_default="{}")
    publish_time = Column(String(50), nullable=True)  # 容错: 存原文, 不强解析
    scraped_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("platform", "platform_id", name="uix_platform_item"),
    )

    # --- Legacy columns retained for S4/S7 consumers (to be dropped later) ---
    # NOTE: declared after __table_args__ is fine — SQLAlchemy collects all Column attrs.
    keyword_id = Column(Integer, nullable=True)  # FK dropped (PRD has no keyword_id)
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
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<CollectionItem id={self.id} platform={self.platform} platform_id={self.platform_id}>"
