"""CSV flat-table export of scraped notes + comments (PRD §4.6).

Left-join wide table: one note × N comments → N rows; a note with 0 comments
yields 1 row with the comment columns empty. Reads directly from the shared
SQLite (no worker dependency) off the canonical PRD §6.2/§6.3 columns:
``content_text`` / ``metrics_json`` (parsed for likes) / ``publish_time`` /
``url`` for items; ``author_name`` / ``content_text`` / ``like_count`` for
comments joined by ``item_id``.

- 10 Chinese headers (PRD §4.6.3): 平台/笔记ID/笔记标题/笔记正文/笔记点赞数/
  笔记发布时间/笔记链接/评论者昵称/评论内容/评论点赞数
- ``utf-8-sig`` BOM so Windows Excel opens without mojibake (PRD §4.6.2)
- ``csv.DictWriter`` handles emoji / comma / quote escaping (PRD §8.6 场景6.1)
- 0 records → ``EmptyExportError``; the route layer turns it into a Toast
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semilabs_hone.core.models.db import get_session
from semilabs_hone.core.models.post import CollectionItem
from semilabs_hone.core.models.comment import CollectionComment
from semilabs_hone.core.models.repository import unpack_metrics


class EmptyExportError(Exception):
    """Raised when there are 0 notes to export (PRD §4.6: 0 条 → 拦截 + Toast)."""


# PRD §4.6.3 — 10 列中文表头
HEADERS: list[str] = [
    "平台", "笔记ID", "笔记标题", "笔记正文", "笔记点赞数",
    "笔记发布时间", "笔记链接", "评论者昵称", "评论内容", "评论点赞数",
]


def _likes_of(item: CollectionItem) -> int:
    """笔记点赞数 ← metrics_json.likes (PRD §6.4 TEXT)."""
    try:
        return int(unpack_metrics(item.metrics_json).get("likes", 0) or 0)
    except Exception:
        return 0


def _query_items(task_id: str | None) -> list[CollectionItem]:
    """Return items (notes) matching the task filter, ordered by likes desc."""
    sess = get_session()
    try:
        q = sess.query(CollectionItem)
        if task_id is not None:
            q = q.filter(CollectionItem.task_id == task_id)
        items = q.all()
        # PRD §4.6.1: 默认按点赞数降序
        items.sort(key=lambda it: _likes_of(it), reverse=True)
        return items
    finally:
        sess.close()


def _query_comments_by_item(item_ids: list[str]) -> dict[str, list[CollectionComment]]:
    """Return {item_id: [comments]} for the given items, comments by like_count desc."""
    if not item_ids:
        return {}
    sess = get_session()
    try:
        rows = (
            sess.query(CollectionComment)
            .filter(CollectionComment.item_id.in_(item_ids))
            .order_by(CollectionComment.like_count.desc())
            .all()
        )
    finally:
        sess.close()
    mapping: dict[str, list[CollectionComment]] = {}
    for c in rows:
        mapping.setdefault(c.item_id, []).append(c)
    return mapping


def _build_rows(items: list[CollectionItem]) -> list[dict[str, str]]:
    """Left-join wide-table rows (PRD §4.6.2): 1 note × N comments → N rows;
    0 comments → 1 row with empty comment columns."""
    comments_map = _query_comments_by_item([it.id for it in items])
    rows: list[dict[str, str]] = []
    for it in items:
        likes = _likes_of(it)
        base = {
            "平台": it.platform or "",
            "笔记ID": it.platform_id or "",
            "笔记标题": it.title or "",
            "笔记正文": it.content_text or "",
            "笔记点赞数": str(likes),
            "笔记发布时间": it.publish_time or "",
            "笔记链接": it.url or "",
            "评论者昵称": "",
            "评论内容": "",
            "评论点赞数": "",
        }
        comments = comments_map.get(it.id, [])
        if not comments:
            # 0 评论 → 1 行，评论列留空 (PRD §4.6.2)
            rows.append(base)
            continue
        for c in comments:
            row = dict(base)
            row["评论者昵称"] = c.author_name or ""
            row["评论内容"] = c.content_text or ""
            row["评论点赞数"] = str(c.like_count or 0)
            rows.append(row)
    return rows


def _write_csv(rows: list[dict[str, str]], path: Path) -> None:
    """Write rows to a utf-8-sig CSV with Chinese headers (PRD §4.6.2/§8.6)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_csv(task_id: str | None = None) -> Path:
    """Export scraped notes + comments as a left-join wide-table CSV (PRD §4.6).

    Args:
        task_id: filter by collection task ID (None = all tasks).

    Returns:
        Path to the exported ``.csv`` file.

    Raises:
        EmptyExportError: when 0 notes match the filter (PRD §4.6: 拦截 + Toast).
    """
    from config import DATA_DIR

    export_dir = DATA_DIR / "collection" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    items = _query_items(task_id)
    if not items:
        raise EmptyExportError("暂无可导出的采集数据")

    rows = _build_rows(items)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = export_dir / f"export_{timestamp}.csv"
    _write_csv(rows, out_path)
    return out_path
