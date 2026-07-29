"""CollectionTask + TaskKeyword ORM models (collection_tasks table).

PRD §6.1 — canonical task table:
    id(UUID v4 str36) PK · platform · task_type · target_value · status
    · expected_count · actual_count · error_msg · created_at · updated_at

[契约变更 2026-07-29] 主线合并收官：S3 过渡保留的旧 ScrapeTask 列
（account_id/max_posts_per_keyword/posts_scraped/last_note_index/sort_type/
download_images/collect_comments/error_message/error_category/started_at/
completed_at）已全部删除——handlers/routes/watchdog/模板消费者均已切到
PRD 列名。任务→账号关联不再落库（PRD §6.1 无此列）；worker 账号由 IPC
payload 携带、resume 时按平台解析 active 账号。本地工具无迁移脚本：
先 /api/export 备份 CSV，再删 data/factory.db 重启重建。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, PrimaryKeyConstraint

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

    # --- S6 addition: latest IPC request_id (for badge↔progress correlation &
    #     future resume→control/ctrl_<rid>.json wiring, PRD §4.4.3). Nullable so
    #     legacy seedings / pre-S6 rows stay valid. ---
    request_id = Column(String(12), nullable=True)

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
