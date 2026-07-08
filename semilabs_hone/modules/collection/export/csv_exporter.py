"""CSV / Excel export of scraped posts + comments.

Reads directly from shared SQLite (no worker dependency).

- AI mode: single CSV with top_comments as "Author:Content(N likes)" pipe-joined.
- Excel mode: ZIP containing posts.csv + comments.csv linked by note_id.
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semilabs_hone.core.models.db import get_session
from semilabs_hone.core.models.post import Post
from semilabs_hone.core.models.comment import Comment
from semilabs_hone.core.models.keyword import Keyword
from semilabs_hone.core.models.task import ScrapeTask, TaskKeyword


# ---------------------------------------------------------------------------
# AI mode: single CSV
# ---------------------------------------------------------------------------

_AI_FIELDS = [
    "note_id", "url", "title", "author", "content", "tags",
    "post_type", "likes", "collects", "comments_count", "shares",
    "published_at", "keyword", "image_count", "top_comments", "scraped_at",
]


def _fmt_dt(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)


def _build_top_comments(comments: list[Comment]) -> str:
    """Return pipe-separated "Author:Content(N likes)" for top comments."""
    parts: list[str] = []
    for c in sorted(comments, key=lambda x: x.likes or 0, reverse=True):
        name = c.author_name or ""
        body = (c.content or "").replace("|", "｜")
        likes = c.likes or 0
        parts.append(f"{name}:{body}({likes} likes)")
    return "|".join(parts)


def _query_posts(
    task_id: int | None,
    keyword: str | None,
) -> list[tuple[Post, str, list[Comment]]]:
    """Return list of (post, keyword_text, [comments]) matching filters."""
    sess = get_session()
    try:
        q = sess.query(Post)

        if task_id is not None:
            q = q.filter(Post.task_id == task_id)

        if keyword is not None:
            # Find keyword_id(s) matching the text
            kw_sub = sess.query(Keyword.id).filter(Keyword.text == keyword)
            q = q.filter(Post.keyword_id.in_(kw_sub))

        posts = q.order_by(Post.id).all()

        # Fetch comments for these posts in one query
        post_ids = [p.id for p in posts]
        comments_map: dict[int, list[Comment]] = {}
        if post_ids:
            comments_list = sess.query(Comment).filter(Comment.post_id.in_(post_ids)).all()
            for c in comments_list:
                comments_map.setdefault(c.post_id, []).append(c)

        # Resolve keyword text per post
        keyword_map: dict[int, str] = {}
        if posts:
            # Build mapping: keyword_id -> text
            kw_ids = {p.keyword_id for p in posts if p.keyword_id is not None}
            if kw_ids:
                kws = sess.query(Keyword).filter(Keyword.id.in_(kw_ids)).all()
                keyword_map = {kw.id: kw.text for kw in kws}

        result: list[tuple[Post, str, list[Comment]]] = []
        for p in posts:
            kw_text = keyword_map.get(p.keyword_id, "") if p.keyword_id is not None else ""
            result.append((p, kw_text, comments_map.get(p.id, [])))

        return result
    finally:
        sess.close()


def _ai_csv_rows(
    posts_data: list[tuple[Post, str, list[Comment]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for post, kw_text, comments in posts_data:
        tags_val = (post.tags or "").replace("|", "｜")
        rows.append({
            "note_id": post.platform_id or "",
            "url": post.url or "",
            "title": post.title or "",
            "author": post.author_name or "",
            "content": (post.content or "").replace("|", "｜"),
            "tags": tags_val,
            "post_type": post.post_type or "",
            "likes": post.likes or 0,
            "collects": post.collects or 0,
            "comments_count": post.comments_count or 0,
            "shares": post.shares or 0,
            "published_at": _fmt_dt(post.published_at),
            "keyword": kw_text,
            "image_count": post.image_count or 0,
            "top_comments": _build_top_comments(comments),
            "scraped_at": _fmt_dt(post.scraped_at),
        })
    return rows


def _write_ai_csv(rows: list[dict[str, str]], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_AI_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Excel mode: ZIP with posts.csv + comments.csv
# ---------------------------------------------------------------------------

_POST_EXCEL_FIELDS = [
    "note_id", "url", "title", "author", "content", "tags",
    "post_type", "likes", "collects", "comments_count", "shares",
    "published_at", "keyword", "image_count", "scraped_at",
]

_COMMENT_EXCEL_FIELDS = [
    "note_id", "author", "content", "likes", "sub_comment_count",
    "is_author_liked", "rank", "published_at",
]


def _write_excel_zip(
    posts_data: list[tuple[Post, str, list[Comment]]],
    path: Path,
) -> None:
    posts_rows: list[dict[str, str]] = []
    comments_rows: list[dict[str, str]] = []

    for post, kw_text, comments in posts_data:
        note_id = post.platform_id or ""
        tags_val = (post.tags or "").replace("|", "｜")
        posts_rows.append({
            "note_id": note_id,
            "url": post.url or "",
            "title": post.title or "",
            "author": post.author_name or "",
            "content": (post.content or "").replace("|", "｜"),
            "tags": tags_val,
            "post_type": post.post_type or "",
            "likes": post.likes or 0,
            "collects": post.collects or 0,
            "comments_count": post.comments_count or 0,
            "shares": post.shares or 0,
            "published_at": _fmt_dt(post.published_at),
            "keyword": kw_text,
            "image_count": post.image_count or 0,
            "scraped_at": _fmt_dt(post.scraped_at),
        })
        for c in comments:
            comments_rows.append({
                "note_id": note_id,
                "author": c.author_name or "",
                "content": (c.content or "").replace("|", "｜"),
                "likes": c.likes or 0,
                "sub_comment_count": c.sub_comment_count or 0,
                "is_author_liked": str(bool(c.is_author_liked)),
                "rank": c.rank or "",
                "published_at": _fmt_dt(c.published_at),
            })

    def _csv_str(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buf.getvalue().encode("utf-8-sig")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("posts.csv", _csv_str(_POST_EXCEL_FIELDS, posts_rows))
        zf.writestr("comments.csv", _csv_str(_COMMENT_EXCEL_FIELDS, comments_rows))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_csv(
    task_id: int | None = None,
    keyword: str | None = None,
    fmt: str = "ai",
) -> Path:
    """Export scraped data to CSV or Excel ZIP.

    Args:
        task_id: filter by scrape task ID (None = all).
        keyword: filter by keyword text (None = all).
        fmt: "ai" (single CSV) or "excel" (ZIP with posts.csv+comments.csv).

    Returns:
        Path to the exported file.
    """
    from config import DATA_DIR

    export_dir = DATA_DIR / "collection" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    posts_data = _query_posts(task_id, keyword)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt == "excel":
        out_path = export_dir / f"export_excel_{timestamp}.zip"
        _write_excel_zip(posts_data, out_path)
    else:
        out_path = export_dir / f"export_ai_{timestamp}.csv"
        rows = _ai_csv_rows(posts_data)
        _write_ai_csv(rows, out_path)

    return out_path


def export_empty_db(fmt: str = "ai") -> Path:
    """Export from an empty database without raising.

    Returns a valid (empty-data) file.
    """
    from config import DATA_DIR

    export_dir = DATA_DIR / "collection" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if fmt == "excel":
        out_path = export_dir / f"export_excel_{timestamp}.zip"
        _write_excel_zip([], out_path)
    else:
        out_path = export_dir / f"export_ai_{timestamp}.csv"
        _write_ai_csv([], out_path)

    return out_path
