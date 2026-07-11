"""PRD §8.7 — 节律控制与养号红线验收 (Rhythm & Account Safety).

BDD acceptance tests for scenarios 7.1 (全局日限额跨任务累加) and 7.2 (随机延迟有效性).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest


# ─── 场景 7.1：全局日限额跨任务累加拦截 ─────────────────────────────────

class TestScenario71DailyQuotaAccumulation:
    """PRD §8.7 场景 7.1.

    Given 今日养号安全上限为 200 条。上午执行【任务 A】已成功抓取入库 150 条.
    When  下午用户启动【任务 B】，设定采集 100 条.
    Then  【任务 B】在执行到第 50 条时，SQLite 检测到当天总入库量达到 200.
          And  Worker 必须主动挂起【任务 B】，状态转为 paused，UI 提示
          「全局日配额已达上限，保护机制生效，请明日恢复」.
    """

    async def _seed_today_items(self, db_session, n, *, platform="xiaohongshu"):
        """Insert n collection_items dated today (simulating task A's morning run)."""
        from semilabs_hone.core.models.post import CollectionItem
        now = datetime.now()
        for i in range(n):
            db_session.add(CollectionItem(
                platform=platform, platform_id=f"seedA_{i}",
                title=f"seed {i}", content_text="c", scraped_at=now))
        db_session.commit()

    async def test_task_b_pauses_when_daily_total_hits_200(self, db_session, tmp_data_dir, monkeypatch):
        """Task A seeded 150 today; Task B runs → parks as paused when total reaches 200.

        PRD 7.1 Then: SQLite 检测到当天总入库量达到 200 → 状态转为 paused + 文案.
        Drives the REAL _check_rhythm (S8 wiring: SQLite COUNT today, raises
        DailyLimitError, not swallowed) — only _get_engine / night-sleep are stubbed.
        """
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.schemas import ScrapedPost
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod
        from tests.prd_bdd.conftest import _make_task, _noop_async

        # Given: task A already scraped 150 items today.
        await self._seed_today_items(db_session, 150)

        class FakeRef:
            def __init__(self, i):
                self.item_id = f"b_{i}"

        async def mock_search(keyword, sort):
            # Task B wants 100 — enough to push the daily total past 200.
            return [FakeRef(i) for i in range(100)]

        async def mock_fetch_item(ref):
            return ScrapedPost(platform_id=ref.item_id, title="t", content="c")

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None  # warmup becomes a no-op (no random_browse)

        task_id = _make_task(db_session, max_posts=100)
        # Stub engine + night-sleep, but leave _check_rhythm REAL (drives the cap).
        orig_engine = h_mod._get_engine
        orig_night = h_mod._night_sleep_if_quiet
        h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
        h_mod._night_sleep_if_quiet = _noop_async
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 100, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "bdd-7-1",
            }, cap)

            # Then: task parked as paused (not ok, not need_human) at the cap.
            assert result["status"] == "paused"
            assert result["reason"] == "daily_limit"
            # 50 items scraped (150 + 50 = 200) before the cap tripped the next ref.
            assert result["posts_scraped"] == 50
            # UI-facing progress event + message surfaced.
            assert any(m == "daily_limit" for m, _ in progress)
            daily_events = [d for m, d in progress if m == "daily_limit"]
            assert any("全局日配额" in (d or {}).get("msg", "") for d in daily_events)

            # And: DB task status flipped to paused.
            sess = get_session()
            try:
                task = sess.query(CollectionTask).filter(
                    CollectionTask.id == task_id).first()
                assert task.status == "paused"
            finally:
                sess.close()
        finally:
            h_mod._get_engine = orig_engine
            h_mod._night_sleep_if_quiet = orig_night


# ─── 场景 7.2：随机延迟机制有效性 ───────────────────────────────────────

class TestScenario72RandomDelay:
    """PRD §8.7 场景 7.2.

    Given 连续抓取 3 篇笔记.
    When  检查系统执行日志.
    Then  每篇笔记之间的停留时间必须是截然不同的浮点数（如 45.2s, 61.8s, 34.5s），
          绝不能是固定的 sleep(60).
    """

    async def test_three_note_delays_are_distinct_floats_in_range(self, monkeypatch):
        """3 consecutive note_delay() calls yield distinct floats ∈ [30, 90].

        PRD 7.2 Then: 截然不同的浮点数，绝不能是固定的 sleep(60).
        """
        from semilabs_hone.modules.collection.scheduler import rhythm

        recorded: list[float] = []

        async def fake_sleep(seconds):
            recorded.append(seconds)

        # Real random.uniform (distinctness under test); only neutralize the sleep.
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        for _ in range(3):
            await rhythm.note_delay()

        assert len(recorded) == 3
        # All in the configured [30, 90] band.
        assert all(30 <= d <= 90 for d in recorded)
        # Distinct — never a fixed sleep(60) repeated.
        assert len(set(recorded)) == 3
