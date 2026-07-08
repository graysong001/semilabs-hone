"""Pydantic v2 schemas for API I/O and WebSocket contracts.

Separate from ORM models: engine outputs ScrapedPost/ScrapedComment,
handlers are responsible for ORM upsert.
"""
from pydantic import BaseModel, Field
from typing import Literal


# ---------------------------------------------------------------------------
# API I/O
# ---------------------------------------------------------------------------

class AccountCreate(BaseModel):
    platform: str = "xiaohongshu"
    nickname: str


class TaskCreate(BaseModel):
    account_id: int
    platform: str
    keywords: list[str]
    sort: str = "general"
    max_posts_per_keyword: int = 20
    download_images: bool = True
    collect_comments: bool = True


# ---------------------------------------------------------------------------
# WebSocket contract (ProgressMessage, §13.3)
# ---------------------------------------------------------------------------

class ProgressMessage(BaseModel):
    type: Literal[
        "progress", "warn", "qr_ready", "login_required", "login_success",
        "captcha_required", "task_completed", "error", "disk_warn"
    ]
    module: str | None = None
    task_id: int | None = None
    account_id: int | None = None
    message: str
    severity: Literal["info", "warn", "error"] = "info"
    category: str | None = None
    data: dict | None = None
    timestamp: float


# ---------------------------------------------------------------------------
# Scraped pipeline data classes (engine output, not ORM)
# ---------------------------------------------------------------------------

class ItemRef(BaseModel):
    platform: str
    item_id: str
    title: str | None = None
    author_name: str | None = None
    likes: int | None = None


class ScrapedPost(BaseModel):
    title: str | None = None
    content: str | None = None
    author_name: str | None = None
    image_urls: list[str] | None = None
    tags: list[str] | None = None
    published_at: str | None = None
    likes: int | None = None
    collects: int | None = None
    comments_count: int | None = None
    shares: int | None = None
    post_type: str | None = None
    platform_id: str | None = None


class ScrapedComment(BaseModel):
    platform_id: str | None = None
    author_name: str | None = None
    content: str
    likes: int | None = None
    rank: int | None = None
