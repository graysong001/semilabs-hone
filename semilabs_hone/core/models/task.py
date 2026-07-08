"""ScrapeTask and TaskKeyword ORM models.

ScrapeTask: spec §4.3 + §7.1 revisions (download_images, collect_comments).
TaskKeyword: spec §4.4, composite primary key.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, PrimaryKeyConstraint

from semilabs_hone.core.models.db import Base


class ScrapeTask(Base):
    __tablename__ = "scrape_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    platform = Column(String(20), nullable=False, default="xiaohongshu")
    status = Column(String(20), nullable=False, default="pending")
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
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<ScrapeTask id={self.id} status={self.status}>"


class TaskKeyword(Base):
    __tablename__ = "task_keywords"

    task_id = Column(Integer, ForeignKey("scrape_tasks.id"), primary_key=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), primary_key=True)

    __table_args__ = (
        PrimaryKeyConstraint("task_id", "keyword_id"),
    )

    def __repr__(self):
        return f"<TaskKeyword task_id={self.task_id} keyword_id={self.keyword_id}>"
