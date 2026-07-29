"""PRD §8.4 — 浏览器控制与页面导航验收 (Browser Navigation).

BDD acceptance tests for scenarios 4.1 (Timeout 崩溃防御) and 4.2 (无限滚动边界).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from semilabs_hone.core.models.schemas import ItemRef
from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
from semilabs_hone.modules.collection.scrapers.spec import (
    Flow,
    LoginSpec,
    PlatformSpec,
    Step,
)

# Reuse the battle-tested page mock + spec builder from test_engine (identical
# XHR-firing behavior via call_soon; avoids re-deriving the response listener).
from tests.collection.test_engine import MockResponse, _ScrollPage, _make_scroll_collect_spec  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


# ─── 场景 4.1：页面级 Timeout 崩溃防御 ──────────────────────────────────

class TestScenario41TimeoutSkip:
    """PRD §8.4 场景 4.1.

    Given 平台服务器卡顿，或用户本地网络极差.
    When  Worker 执行 page.goto(url) 或等待某个 Selector 时超时（超出 30 秒）.
    Then  Playwright 会抛出 TimeoutError. Worker 必须捕获此异常，不中断任务.
          记录错误日志，进度计数器 +1（作为无效件消耗），然后继续执行下一条 URL.
    """

    async def test_timeout_on_one_url_skips_and_continues(self, db_session, tmp_data_dir):
        """A page.goto/selector TimeoutError on one ref is skipped; the next URL runs.

        PRD 4.1 Then: 捕获 TimeoutError + 不中断任务 + 进度计数器 +1 + 继续下一条.
        """
        from semilabs_hone.core.models.schemas import ScrapedPost
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod
        from tests.prd_bdd.conftest import _patch_handler_env, _restore_handler_env, _make_task

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        attempts = {"n": 0}

        async def mock_fetch_item(ref):
            attempts["n"] += 1
            if attempts["n"] == 1:
                # Simulate page.goto / wait_for_selector timing out (>30s).
                raise TimeoutError("page.goto: timeout 30000ms exceeded")
            return ScrapedPost(platform_id=ref.item_id, title="ok", content="c")

        async def mock_search(keyword, sort):
            return [FakeRef("bad"), FakeRef("good")]

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=10)
        orig = _patch_handler_env(h_mod, mock_engine)
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 10, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "bdd-4-1",
            }, cap)

            # Then: task not interrupted; the good URL was still processed.
            assert result["status"] == "ok"
            assert result["posts_scraped"] == 1
            # The timed-out ref was logged as a skip (invalid件 consumed).
            assert any(m == "detail_skip_error" for m, _ in progress)
            # Both refs were attempted (bad skipped, good stored) — continue next URL.
            assert attempts["n"] == 2
        finally:
            _restore_handler_env(h_mod, orig)


# ─── 场景 4.2：无限滚动列表防死锁 ───────────────────────────────────────


class TestScenario42InfiniteScrollBoundary:
    """PRD §8.4 场景 4.2.

    Given 某个达人主页底部加载动画一直转圈，DOM 结构无法闭合.
    When  Worker 在列表中执行向下滚动提取 URL.
    Then  Worker 必须设置硬性最大滚动次数（如 20 次）。达到 20 次且无新 URL 出现时，
          必须强制跳出滚动循环，进入详情页提取阶段，绝不能陷入死循环.
    """

    async def test_empty_break_breaks_loop_before_max(self, monkeypatch):
        """5 consecutive empty scrolls break the loop — well under the 20 cap.

        PRD 4.2 Then: 无新 URL 出现时必须强制跳出，绝不陷入死循环.
        """
        spec = _make_scroll_collect_spec(max_scrolls=20, empty_break=5)
        engine = GenericEngine(spec=spec)
        page = _ScrollPage(_load_fixture("search_response.json"))
        engine.page = page

        scroll_calls = []

        async def fake_scroll(p, mt, wms):
            scroll_calls.append(mt)

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior.random_scroll",
            fake_scroll,
        )
        await engine.run_flow("search", keyword="x")
        # Broke after 5 consecutive empty — bounded, no death loop.
        assert len(scroll_calls) == 5
        assert len(scroll_calls) <= 20

    async def test_max_scrolls_hard_cap_enforced(self, monkeypatch):
        """max_scrolls=20 is a hard cap even when empty_break never triggers.

        PRD 4.2 Then: 设置硬性最大滚动次数（如 20 次），达到后强制跳出.
        """
        spec = _make_scroll_collect_spec(max_scrolls=20, empty_break=999)
        engine = GenericEngine(spec=spec)
        page = _ScrollPage(_load_fixture("search_response.json"))
        engine.page = page

        scroll_calls = []

        async def fake_scroll(p, mt, wms):
            scroll_calls.append(mt)

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.human_behavior.random_scroll",
            fake_scroll,
        )
        await engine.run_flow("search", keyword="x")
        # Hard cap: never exceeds 20, never loops forever.
        assert len(scroll_calls) == 20
