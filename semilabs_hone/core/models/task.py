"""CollectionTask + TaskKeyword ORM models (collection_tasks table).

PRD §6.1 — canonical task table:
    id(UUID v4 str36) PK · platform · task_type · target_value · status
    · expected_count · actual_count · error_msg · created_at · updated_at

[契约变更 2026-07-10] S3 原地改表过渡：上 PRD §6 新列 + UUID PK，同时
**保留**旧 ScrapeTask 字段（account_id/max_posts_per_keyword/posts_scraped/
last_note_index/sort_type/download_images/collect_comments/error_message/
error_category/started_at/completed_at），供 handlers/routes/csv/tests 等
S4/S6/S7 消费者继续使用，零逻辑改动。旧列在 S4/S6/S7 重写各自消费者时
切到 PRD 列名并从模型删除（create_all 重建即生效）。account_id 的旧 FK
已移除（PRD collection_tasks 无 account_id），仅保留裸列。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, PrimaryKeyConstraint

from semilabs_hone.core.models.db import Base


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionTask(Base):
    __tablename__ = "collection_tasks"

    # --- PRD §6.1 canonical columns ---
    id = Column(String(36), primary_key=True, default=_uuid4_str)
    platform = Column(String(20), nullable=False, default="xiaohongshu")
    task_type = Column(String(20), nullable=False, default="keyword_search")  # keyword_search | author_homepage
    target_value = Column(String(255), nullable=False, default="")
    status = Column(String(20), nullable=False, default="pending")
    expected_count = Column(Integer, nullable=False, default=0)
    actual_count = Column(Integer, nullable=False, default=0)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)

    # --- Legacy columns retained for S4/S6/S7 consumers (to be dropped later) ---
    account_id = Column(Integer, nullable=True)  # FK dropped (PRD has no account_id)
    max_posts_per_keyword = Column(Integer, nullable=False, default=20)
    posts_scraped = Column(Integer, nullable=False, default=0)
    last_note_index = Column(Integer, nullable=False, default=0)
    sort_type = Column(String(30), nullable=False, default="general")
    download_images = Column(Boolean, nullable=False, default=True)
    collect_comments = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    error_category = Column(String(30), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<CollectionTask id={self.id} status={self.status}>"


class TaskKeyword(Base):
    __tablename__ = "task_keywords"

    task_id = Column(String(36), ForeignKey("collection_tasks.id"), primary_key=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), primary_key=True)

    __table_args__ = (
        PrimaryKeyConstraint("task_id", "keyword_id"),
    )

    def __repr__(self):
        return f"<TaskKeyword task_id={self.task_id} keyword_id={self.keyword_id}>"
