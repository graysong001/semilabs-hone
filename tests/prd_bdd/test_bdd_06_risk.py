"""PRD §8.6 — 风控拦截与人工接力验收 (Anti-bot & Human Relay).

BDD acceptance tests for scenarios 6.1 (动态会话过期) and 6.2 (防暴力重试红线).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from semilabs_hone.modules.collection.captcha import solver as solver_mod
from semilabs_hone.modules.collection.captcha.solver import (
    SolveResult,
    detect_and_solve,
)


# ─── 场景 6.1：动态会话过期处理 ─────────────────────────────────────────

class TestScenario61SessionExpiry:
    """PRD §8.6 场景 6.1.

    Given 任务进度 100/200 时，平台安全策略强制当前账号下线，页面跳转至 /login
          且带有扫码框.
    When  Worker 执行下一步动作前的风控探针检测.
    Then  探针必须识别 URL 变化或登录 DOM，立即挂起任务为 need_human.
    """

    async def test_xhs_login_redirect_detected_by_probe(self):
        """An XHS /login redirect mid-task is detected as login_expired.

        PRD 6.1 Then: 探针必须识别 URL 变化.
        """
        from semilabs_hone.modules.collection.risk_probes import probe

        class _Page:
            url = "https://www.xiaohongshu.com/login?from=explore"

            async def query_selector(self, sel):
                return None

        hit = await probe(_Page(), "xiaohongshu")
        assert hit is not None
        assert hit.kind == "login_expired"

    async def test_handler_parks_need_human_on_login_expired_hit(self, db_session, tmp_data_dir):
        """A login_expired RiskProbeHit mid-task parks the task (need_human progress fires).

        PRD 6.1 Then: 立即挂起任务为 need_human. The probe hit sinks the task to
        need_human + broadcasts; after a human resume the worker re-probes and
        retries the same ref (PRD §4.4.3). Here resume is simulated (no real poll)
        and the 2nd attempt succeeds, proving the park-then-resume flow.
        """
        from semilabs_hone.core.models.schemas import ScrapedPost
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        from semilabs_hone.modules.collection.risk_probes import ProbeHit
        from semilabs_hone.modules.collection.scrapers.engine import RiskProbeHit
        import semilabs_hone.modules.collection.handlers as h_mod
        from tests.prd_bdd.conftest import _patch_handler_env, _restore_handler_env, _make_task, _noop_async

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        attempts = {"n": 0}
        parked = {"called": False}
        orig_park = h_mod._set_task_need_human

        def spy_park(task_id, progress_cb):
            parked["called"] = True
            return orig_park(task_id, progress_cb)

        async def mock_search(keyword, sort):
            return [FakeRef("n_login")]

        async def mock_fetch_item(ref):
            attempts["n"] += 1
            if attempts["n"] == 1:
                # Session dropped mid-task → probe hits login_expired.
                raise RiskProbeHit(ProbeHit(kind="login_expired", platform="xiaohongshu"))
            return ScrapedPost(platform_id=ref.item_id, title="t", content="c")

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=5)
        orig = _patch_handler_env(h_mod, mock_engine)
        orig_await = h_mod._await_resume
        h_mod._await_resume = _noop_async  # simulate instant human resume
        h_mod._set_task_need_human = spy_park
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 5, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "bdd-6-1",
            }, cap)

            # Then: probe hit parked the task as need_human (progress + setter fired),
            # then resume retried the same ref and succeeded.
            assert any(m == "need_human" for m, _ in progress)
            assert parked["called"] is True
            assert attempts["n"] == 2  # 1st hit → park; 2nd → success
            assert result["status"] == "ok"
        finally:
            h_mod._await_resume = orig_await
            h_mod._set_task_need_human = orig_park
            _restore_handler_env(h_mod, orig)


# ─── 场景 6.2：防暴力重试红线 ─────────────────────────────────────────

SLIDE_SEL = '[class*="slide"]'


def _async_counter(counter: dict, key: str, ret):
    async def _fake(*args, **kwargs):
        counter[key] += 1
        return ret
    return _fake


class TestScenario62NoBruteForceRedline:
    """PRD §8.6 场景 6.2.

    Given 触发了滑块验证码.
    When  代码逻辑走到此处.
    Then  如果代码中存在对包含 captcha 元素的 element.click() 或 while is_captcha:
          不断刷新页面的死循环，代码审查直接不通过.
    """

    def test_linter_flags_while_is_captcha_death_loop(self):
        """check_constraints encodes `while is_captcha:` as a redline violation.

        PRD 6.2 Then: while is_captcha: 不断刷新页面的死循环 → 代码审查不通过.
        The constitutional linter (scripts/check_constraints.py) enforces this
        as a forbidden pattern — a sample source with the death loop is matched.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "check_constraints", str(Path("scripts/check_constraints.py")))
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)

        sample = (
            "while is_captcha:\n"
            "    page.reload()\n"
            "    element.click()\n"
        )
        # The linter's FORBIDDEN table must contain a rule that matches the
        # `while is_captcha:` death loop.
        matched = [msg for pat, msg in cc.FORBIDDEN if re.search(pat, sample)]
        assert matched, "linter must encode the while is_captcha: death-loop redline"
        assert any("is_captcha" in m or "死循环" in m for m in matched)

    async def test_manual_policy_never_auto_solves(self, monkeypatch):
        """Default manual policy: captcha hit → paused immediately, zero auto attempts.

        PRD 6.2 Then (no brute force): manual never calls solve_slide/solve_ocr.
        """
        calls = {"slide": 0, "ocr": 0}
        monkeypatch.setattr(solver_mod.slide_solver, "solve_slide",
                            _async_counter(calls, "slide", ret=False))
        monkeypatch.setattr(solver_mod.ocr_solver, "solve_ocr",
                            _async_counter(calls, "ocr", ret=""))

        class _Page:
            async def query_selector(self, sel):
                return object() if sel == SLIDE_SEL else None

        res = await detect_and_solve(_Page())  # default = manual
        assert res.status == "paused"
        assert calls["slide"] == 0
        assert calls["ocr"] == 0

    async def test_auto_then_manual_solves_exactly_once_no_loop(self, monkeypatch):
        """anonymous + auto_then_manual: one failed attempt → paused. No death loop.

        PRD 6.2 Then: 失败 1 次转人工、不死循环 (solve called exactly once).
        """
        calls = {"slide": 0}
        monkeypatch.setattr(solver_mod.slide_solver, "solve_slide",
                            _async_counter(calls, "slide", ret=False))

        class _Page:
            async def query_selector(self, sel):
                return object() if sel == SLIDE_SEL else None

        res = await detect_and_solve(
            _Page(), risk_tier="anonymous", captcha_policy="auto_then_manual")
        assert res.status == "paused"
        assert calls["slide"] == 1  # not 2, not N — no death loop
