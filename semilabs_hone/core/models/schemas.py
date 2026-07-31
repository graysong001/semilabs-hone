"""Pydantic v2 schemas for API I/O and WebSocket contracts.

Separate from ORM models: engine outputs ScrapedPost/ScrapedComment,
handlers are responsible for ORM upsert.

PRD §4.1 / §6.1 — TaskCreate aligns to the canonical task shape:
    platform · task_type(keyword_search|author_homepage) · target_value
    · expected_count(clamped to [1, 200])
Validation: author_homepage tasks must carry an http(s):// URL target_value.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal


# ---------------------------------------------------------------------------
# API I/O
# ---------------------------------------------------------------------------

class AccountCreate(BaseModel):
    """[契约变更 2026-07-13 S10] Account 建壳 payload。
    - platform: 平台名（下拉选，默认 xiaohongshu）
    - remark: 账号备注（NOT NULL，用户裁决必填；原 nickname 改名）
    """
    platform: str = "xiaohongshu"
    remark: str


class TaskCreate(BaseModel):
    """Canonical task creation payload (PRD §4.1, §6.1).

    - task_type=author_homepage requires target_value to be an http(s):// URL.
    - expected_count is clamped (截断) into [1, 200] rather than rejected, so a
      fat-fingered 500 still yields a runnable 200-count task.
    """
    platform: str = "xiaohongshu"
    task_type: Literal["keyword_search", "author_homepage"] = "keyword_search"
    target_value: str = ""
    expected_count: int = 20

    @field_validator("expected_count")
    @classmethod
    def _clamp_count(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 200:
            return 200
        return v

    @model_validator(mode="after")
    def _validate_target(self):
        if not self.target_value:
            raise ValueError("target_value 不能为空")
        if self.task_type == "author_homepage":
            if not (
                self.target_value.startswith("http://")
                or self.target_value.startswith("https://")
            ):
                raise ValueError("author_homepage 任务的 target_value 必须是 http(s):// 开头的 URL")
        return self


# ---------------------------------------------------------------------------
# WebSocket contract (ProgressMessage, §13.3)
# ---------------------------------------------------------------------------

class ProgressMessage(BaseModel):
    type: Literal[
        "progress", "warn", "qr_ready", "login_required", "login_success",
        "captcha_required", "task_completed", "error", "disk_warn",
        "session_status", "cookie_import_conflict",
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
    # Engine injects platform from spec (F7) so multi-platform rows never
    # cross-tag; without this field pydantic silently drops the injection and
    # storage falls back to the xiaohongshu default.
    platform: str | None = None
    title: str | None = None
    content: str | None = None
    author_name: str | None = None
    image_urls: list[str] | None = None
    tags: list[str] | None = None
    # Platforms publish timestamps as epoch ms/s or ISO strings; storage
    # normalises it (handlers._parse_published_at). A strict str type would
    # ValidationError the whole Post.body group on an epoch int.
    published_at: str | int | float | None = None
    likes: int | None = None
    collects: int | None = None
    comments_count: int | None = None
    shares: int | None = None
    post_type: str | None = None
    platform_id: str | None = None


class ScrapedComment(BaseModel):
    platform: str | None = None
    platform_id: str | None = None
    author_name: str | None = None
    content: str
    likes: int | None = None
    rank: int | None = None
