"""PRD §8.8 — 数据交付与异常导出验收 (Data Export).

BDD acceptance tests for scenarios 8.1 (空数据导出防御) and 8.2 (多行合并与跨平台字符兼容).
"""
from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from semilabs_hone.modules.collection.export.csv_exporter import (
    EmptyExportError,
    export_csv,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/core/test_routes.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_data_dir):
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _seed_item(db_session, *, platform_id, title, content, likes, task_id=None):
    from semilabs_hone.core.models import repository as repo
    return repo.upsert_item(
        db_session, task_id=task_id, platform="xiaohongshu",
        platform_id=platform_id, url=f"https://xhs/{platform_id}",
        title=title, content_text=content, author_name="作者A",
        metrics={"likes": likes, "comments_count": 1},
        publish_time="2026-07-08 14:00:00")


def _seed_comment(db_session, *, item_id, platform_comment_id, author, content, likes):
    from semilabs_hone.core.models import repository as repo
    return repo.upsert_comment(
        db_session, item_id=item_id, platform_comment_id=platform_comment_id,
        author_name=author, content_text=content, like_count=likes)


# ─── 场景 8.1：空数据导出防御 ──────────────────────────────────────────

class TestScenario81EmptyExportDefense:
    """PRD §8.8 场景 8.1.

    Given 任务已完成，但实际入库的数据量为 0（如关键词全网无结果）.
    When  用户点击【导出 CSV】按钮.
    Then  后端必须拦截该请求，并返回特定错误码。前端不出触发下载，而是弹出 Toast
          提示「无有效数据可导出」.
    """

    def test_export_zero_items_returns_400_json_no_download(self, client, db_session):
        """A task with 0 items → GET /api/export returns 400 JSON (Toast), no file body.

        PRD 8.1 Then: 后端拦截 + 特定错误码 + 前端不下载 + Toast.
        """
        # Create a task with no scraped items (关键词全网无结果).
        from semilabs_hone.core.models.task import CollectionTask
        task = CollectionTask(account_id=1, platform="xiaohongshu",
                              status="completed", max_posts_per_keyword=10)
        db_session.add(task)
        db_session.commit()

        resp = client.get("/api/export", params={"task_id": task.id})
        # Then: intercepted with 400, JSON error (no CSV download body).
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("ok") is False
        assert body.get("error")

    def test_export_csv_raises_empty_error_on_no_items(self, db_session):
        """export_csv() raises EmptyExportError when 0 notes match.

        PRD 8.1 Then: 后端必须拦截 (EmptyExportError → route 400).
        """
        with pytest.raises(EmptyExportError):
            export_csv(task_id=None)  # nothing seeded in this isolated DB


# ─── 场景 8.2：多行合并与跨平台字符兼容 ────────────────────────────────

class TestScenario82MultilineCharCompat:
    """PRD §8.8 场景 8.2.

    Given 评论正文中包含 Emoji 表情（😂）、各种语言字符，甚至包含半角逗号 `,` 和双引号 `"``.
    When  导出 CSV.
    Then  Python 的 CSV Writer 必须正确处理转义，生成的 CSV 在 Excel 中打开时，不能
          因为文本里的逗号导致「错行、错列」的问题。编码必须是 utf-8-sig.
    """

    def test_emoji_comma_quote_escaped_and_bom(self, db_session):
        """Emoji + comma + quote survive csv round-trip; utf-8-sig BOM present.

        PRD 8.2 Then: 正确处理转义，不因逗号错行错列，编码 utf-8-sig.
        """
        tricky_comment = '😂 hello,world "quoted" 你好，世界'
        item = _seed_item(db_session, platform_id="x1",
                          title="标题,带逗号", content="正文", likes=42)
        _seed_comment(db_session, item_id=item.id, platform_comment_id="c1",
                      author="评论者", content=tricky_comment, likes=5)

        out = export_csv(task_id=None)
        raw = out.read_bytes()
        # Then: utf-8-sig BOM present (Excel-safe).
        assert raw.startswith(b"\xef\xbb\xbf")

        # Parse the written CSV back — the tricky comment must survive intact,
        # not split across rows/columns (csv.DictWriter quotes/escapes correctly).
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
        assert len(rows) == 1  # one note × one comment = one row, no corruption
        assert rows[0]["评论内容"] == tricky_comment
        assert rows[0]["笔记标题"] == "标题,带逗号"  # comma in title survived

    def test_zero_comments_one_row_blank_comment_cols(self, db_session):
        """A note with 0 comments → one row with blank comment columns (left join).

        PRD §4.6.2 / 8.2 Then: 0 评论→1 行评论列空 (no row explosion, no missing row).
        """
        _seed_item(db_session, platform_id="solo", title="无评论笔记",
                   content="内容", likes=7)
        out = export_csv(task_id=None)
        rows = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
        assert len(rows) == 1
        # Comment columns blank, main columns present.
        assert rows[0]["评论者昵称"] == ""
        assert rows[0]["评论内容"] == ""
        assert rows[0]["笔记标题"] == "无评论笔记"
