"""Collection repository — idempotent upserts for collection_items / collection_comments.

PRD §6.4 DB operations rule:
- Upsert via ``INSERT ... ON CONFLICT(...) DO UPDATE`` (SQLite) so resuming a
  partially-scraped task never inserts duplicates and progress only moves forward.
- ``metrics_json`` stored as TEXT; serialized with ``json.dumps`` in Python and
  deserialized with ``json.loads`` (SQLite has no native JSON type).

This is the canonical write path for the PRD §6.2/§6.3 columns. Legacy write
paths in ``handlers._upsert_post`` (which still target the retained legacy
columns ``content``/``likes``/``post_id``/...) coexist during the S3 transition
and will be migrated onto these upserts in S4.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from semilabs_hone.core.models.post import CollectionItem
from semilabs_hone.core.models.comment import CollectionComment


def pack_metrics(metrics: dict[str, Any] | None) -> str:
    """Serialize an interactions dict to a metrics_json TEXT string.

    PRD §6.4: metrics_json is TEXT; serialize in Python. ``None`` → ``"{}"``.
    Keys commonly include ``likes`` / ``comments_count`` / ``collects`` / ``shares``.
    """
    if not metrics:
        return "{}"
    return json.dumps(metrics, ensure_ascii=False, default=str)


def unpack_metrics(metrics_json: str | None) -> dict[str, Any]:
    """Deserialize a metrics_json TEXT string back to a dict (``None`` → ``{}``)."""
    if not metrics_json:
        return {}
    try:
        data = json.loads(metrics_json)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def upsert_item(
    session,
    *,
    task_id: str | None,
    platform: str,
    platform_id: str,
    url: str | None = None,
    title: str | None = None,
    content_text: str | None = None,
    author_name: str | None = None,
    metrics: dict[str, Any] | None = None,
    publish_time: str | None = None,
    scraped_at: datetime | None = None,
) -> CollectionItem:
    """Upsert one collection_items row keyed by (platform, platform_id).

    On conflict, overwrite the mutable PRD columns (``metrics_json`` and
    ``scraped_at`` per PRD §6.2, plus the other extractable fields) so resuming
    never duplicates a note. Returns the upserted ORM row.
    """
    now = scraped_at or datetime.now(timezone.utc)
    stmt = sqlite_insert(CollectionItem).values(
        task_id=task_id,
        platform=platform,
        platform_id=platform_id,
        url=url,
        title=title,
        content_text=content_text,
        author_name=author_name,
        metrics_json=pack_metrics(metrics),
        publish_time=publish_time,
        scraped_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["platform", "platform_id"],
        set_={
            "title": stmt.excluded.title,
            "content_text": stmt.excluded.content_text,
            "author_name": stmt.excluded.author_name,
            "url": stmt.excluded.url,
            "metrics_json": stmt.excluded.metrics_json,
            "publish_time": stmt.excluded.publish_time,
            "scraped_at": stmt.excluded.scraped_at,
        },
    )
    session.execute(stmt)
    session.commit()
    return (
        session.query(CollectionItem)
        .filter(
            CollectionItem.platform == platform,
            CollectionItem.platform_id == platform_id,
        )
        .first()
    )


def upsert_comment(
    session,
    *,
    item_id: str,
    platform_comment_id: str,
    author_name: str | None = None,
    content_text: str | None = None,
    like_count: int = 0,
    scraped_at: datetime | None = None,
) -> CollectionComment:
    """Upsert one collection_comments row keyed by (item_id, platform_comment_id).

    Per PRD §6.3, re-scraping a note must not double its comments. Returns the
    upserted ORM row.
    """
    now = scraped_at or datetime.now(timezone.utc)
    stmt = sqlite_insert(CollectionComment).values(
        item_id=item_id,
        platform_comment_id=platform_comment_id,
        author_name=author_name,
        content_text=content_text,
        like_count=like_count,
        scraped_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["item_id", "platform_comment_id"],
        set_={
            "author_name": stmt.excluded.author_name,
            "content_text": stmt.excluded.content_text,
            "like_count": stmt.excluded.like_count,
            "scraped_at": stmt.excluded.scraped_at,
        },
    )
    session.execute(stmt)
    session.commit()
    return (
        session.query(CollectionComment)
        .filter(
            CollectionComment.item_id == item_id,
            CollectionComment.platform_comment_id == platform_comment_id,
        )
        .first()
    )
