"""Tests for zero-result guard: _complete_task and handler_scrape_task fail paths.

Verifies:
- _complete_task sets status='failed' when actual_count==0
- _complete_task sets status='completed' when actual_count>0 (no regression)
- handler_scrape_task returns failed when search yields 0 items
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import semilabs_hone.modules.collection.handlers as h_mod


def _cap():
    out = []
    return (lambda m, d=None: out.append((m, d))), out


def _make_task(db_session, *, status="running"):
    from semilabs_hone.core.models.task import CollectionTask
    t = CollectionTask(platform="xiaohongshu", status=status, expected_count=10)
    db_session.add(t)
    db_session.commit()
    return t.id


def _get_task(task_id):
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask
    sess = get_session()
    try:
        return sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
    finally:
        sess.close()


# ─── _complete_task zero-result guard ─────────────────────────────────────

class TestCompleteTaskZeroGuard:
    def test_zero_count_sets_failed(self, db_session, tmp_data_dir):
        """actual_count=0 → status='failed', error_msg set, emits scrape_failed."""
        tid = _make_task(db_session, status="running")
        cap, out = _cap()

        h_mod._complete_task(tid, 0, cap)

        task = _get_task(tid)
        assert task.status == "failed"
        assert task.actual_count == 0
        assert task.error_msg is not None
        assert "未获取到任何数据" in task.error_msg
        # Should have emitted scrape_failed
        assert any(m == "scrape_failed" for m, _ in out)

    def test_positive_count_sets_completed(self, db_session, tmp_data_dir):
        """actual_count>0 → normal completed path (no regression)."""
        tid = _make_task(db_session, status="running")
        cap, out = _cap()

        h_mod._complete_task(tid, 5, cap)

        task = _get_task(tid)
        assert task.status == "completed"
        assert task.actual_count == 5
        assert task.error_msg is None
        # Should NOT have emitted scrape_failed
        assert not any(m == "scrape_failed" for m, _ in out)


# ─── handler_scrape_task zero search results ──────────────────────────────

async def _noop_async(*args, **kwargs):
    return None


class TestHandlerScrapeTaskZeroResult:
    async def test_search_returns_empty_yields_failed(self, db_session, tmp_data_dir, monkeypatch):
        """When search finds 0 items, handler returns failed and persists status."""
        from semilabs_hone.modules.collection.handlers import handler_scrape_task

        # Engine that returns empty search results
        async def mock_search(keyword, sort):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.page = None

        # Create task in DB
        from semilabs_hone.core.models.task import CollectionTask
        task = CollectionTask(platform="xiaohongshu", status="running", expected_count=10)
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        # Create account
        from semilabs_hone.core.models.account import Account
        acct = Account(platform="xiaohongshu", remark="test")
        db_session.add(acct)
        db_session.commit()

        progress_calls = []

        def capture_progress(message, data=None):
            progress_calls.append((message, data))

        # Patch helpers
        original_get_engine = h_mod._get_engine
        original_check_rhythm = h_mod._check_rhythm
        original_night_sleep = h_mod._night_sleep_if_quiet
        h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
        h_mod._check_rhythm = lambda account_id, progress_cb: None
        h_mod._night_sleep_if_quiet = _noop_async

        try:
            payload = {
                "task_id": task_id,
                "platform": "xiaohongshu",
                "keywords": ["nonexistent_topic"],
                "sort": "general",
                "max_posts_per_keyword": 10,
                "download_images": False,
                "collect_comments": False,
                "account_id": 1,
                "request_id": "test-req-zero",
            }

            result = await handler_scrape_task(payload, capture_progress)

            # Must return failed status
            assert result["status"] == "failed"
            assert result["posts_scraped"] == 0
            assert "reason" in result

            # Must have emitted scrape_failed progress
            failed_msgs = [m for m, _ in progress_calls if m == "scrape_failed"]
            assert len(failed_msgs) >= 1

            # DB task must be failed
            task = _get_task(task_id)
            assert task.status == "failed"
            assert task.error_msg is not None
            assert "搜索" in task.error_msg or "未获取" in task.error_msg
        finally:
            h_mod._get_engine = original_get_engine
            h_mod._check_rhythm = original_check_rhythm
            h_mod._night_sleep_if_quiet = original_night_sleep

    async def test_search_returns_items_still_completes(self, db_session, tmp_data_dir, monkeypatch):
        """Positive path: search finds items → completes normally (no regression)."""
        from semilabs_hone.modules.collection.handlers import handler_scrape_task

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        async def mock_search(keyword, sort):
            return [FakeRef("ref1")]

        async def mock_fetch_item(ref):
            from semilabs_hone.core.models.schemas import ScrapedPost
            return ScrapedPost(
                platform_id=ref.item_id,
                platform="xiaohongshu",
                title="Post",
                content="content",
                author_name="Author",
                url="https://test.com/ref1",
                likes=10, collects=5, comments_count=3, shares=1, image_count=0,
            )

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        from semilabs_hone.core.models.task import CollectionTask
        task = CollectionTask(platform="xiaohongshu", status="running", expected_count=10)
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        from semilabs_hone.core.models.account import Account
        acct = Account(platform="xiaohongshu", remark="test")
        db_session.add(acct)
        db_session.commit()

        progress_calls = []
        def capture_progress(message, data=None):
            progress_calls.append((message, data))

        original_get_engine = h_mod._get_engine
        original_check_rhythm = h_mod._check_rhythm
        original_night_sleep = h_mod._night_sleep_if_quiet
        h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
        h_mod._check_rhythm = lambda account_id, progress_cb: None
        h_mod._night_sleep_if_quiet = _noop_async

        try:
            payload = {
                "task_id": task_id,
                "platform": "xiaohongshu",
                "keywords": ["test_kw"],
                "sort": "general",
                "max_posts_per_keyword": 10,
                "download_images": False,
                "collect_comments": False,
                "account_id": 1,
                "request_id": "test-req-ok",
            }

            result = await handler_scrape_task(payload, capture_progress)

            assert result["status"] == "ok"
            assert result["posts_scraped"] >= 1

            # DB task must be completed
            task = _get_task(task_id)
            assert task.status == "completed"
            assert task.actual_count >= 1
        finally:
            h_mod._get_engine = original_get_engine
            h_mod._check_rhythm = original_check_rhythm
            h_mod._night_sleep_if_quiet = original_night_sleep
