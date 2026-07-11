"""CollectionItem ORM model (collection_items table).

PRD §6.2 — canonical item table:
    id(UUID v4 str36) PK · task_id(str36 FK→collection_tasks.id, ON DELETE CASCADE)
    · platform · platform_id · url · title · content_text · author_name
    · metrics_json(TEXT, default '{}') · publish_time(VARCHAR(50), 容错)
    · scraped_at · UNIQUE(platform, platform_id) name=uix_platform_item

[契约变更 2026-07-11 S7] L03 收口：删除 S3 过渡期保留的全部旧列
（content/likes/collects/comments_count/shares/tags/post_type/image_count/
image_urls/local_images/published_at/raw_json/keyword_id/created_at）。
所有消费者已迁到 PRD 列（csv_exporter 读 content_text/metrics_json、
posts 路由/模板读 content_text/metrics_json/publish_time、handlers 走
repository.upsert_item）。旧 UNIQUE(post_id,platform_id)（评论表）同步删除。

注：``url`` 仍 nullable——ScrapedPost schema 无 url 字段、engine 不采集 url、
handler 硬编码 url=None，恢复 NOT NULL 会触发每次 insert IntegrityError。
保留 nullable 并登记遗留 L11，待 engine/schema 补 url 采集后恢复。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint, ForeignKey

from semilabs_hone.core.models.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(String(36), primary_key=True, default=_uuid4_str)
    task_id = Column(String(36), ForeignKey("collection_tasks.id", ondelete="CASCADE"), nullable=True)
    platform = Column(String(20), nullable=False)
    platform_id = Column(String(100), nullable=False)
    url = Column(Text, nullable=True)  # L11: NOT NULL 待 engine 补 url 采集
    title = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    author_name = Column(String(100), nullable=True)
    metrics_json = Column(Text, nullable=False, default="{}", server_default="{}")
    publish_time = Column(String(50), nullable=True)  # 容错: 存原文, 不强解析
    scraped_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("platform", "platform_id", name="uix_platform_item"),
    )

    def __repr__(self):
        return f"<CollectionItem id={self.id} platform={self.platform} platform_id={self.platform_id}>"
