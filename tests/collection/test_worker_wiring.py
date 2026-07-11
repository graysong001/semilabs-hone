"""S9a wiring tests — L14 (ctx→engine), L15 (real QR), L10 (solver wiring).

Covers the worker-published BrowserContext singleton path: set_worker_ctx →
_get_engine injects ctx; _do_qr_login navigates+screenshots when a page is
available (degrades to stub path otherwise); _handle_need_human gives the
solver one shot for anonymous+auto_then_manual platforms and skips it for
account/manual sites like XHS.
"""
from __future__ import annotations

import pytest

import semilabs_hone.modules.collection.handlers as h_mod


# ─── mock page / ctx ──────────────────────────────────────────────────────

class _MockPage:
    """Records goto/wait_for_selector/screenshot for QR + solver wiring tests."""

    def __init__(self) -> None:
        self.goto_calls: list[str] = []
        self.screenshot_calls: int = 0
        self.selector_calls: int = 0

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)

    async def wait_for_selector(self, selector, timeout=5000) -> None:
        self.selector_calls += 1

    async def screenshot(self, path=None, **kw) -> None:
        self.screenshot_calls += 1


class _MockCtx:
    def __init__(self, page) -> None:
        self.pages = [page]

    async def new_page(self):
        return self.pages[0]


@pytest.fixture(autouse=True)
def _reset_worker_ctx():
    """Ensure no ctx leaks between tests (module singleton)."""
    h_mod.set_worker_ctx(None)
    yield
    h_mod.set_worker_ctx(None)


def _cap():
    out: list = []
    return (lambda m, d=None: out.append((m, d))), out


# ─── L14: set_worker_ctx → _get_engine injects ctx ────────────────────────

class TestCtxInjection:
    def test_engine_carries_published_ctx(self, tmp_data_dir):
        try:
            from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
            reg_get("xiaohongshu")
        except KeyError:
            pytest.skip("xiaohongshu not registered")

        fake_ctx = _MockCtx(_MockPage())
        h_mod.set_worker_ctx(fake_ctx)
        eng = h_mod._get_engine("xiaohongshu", None, lambda *a: None)
        assert eng is not None
        assert eng.ctx is fake_ctx  # L14: ctx wired through

    def test_engine_ctx_none_without_worker(self, tmp_data_dir):
        try:
            from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
            reg_get("xiaohongshu")
        except KeyError:
            pytest.skip("xiaohongshu not registered")
        h_mod.set_worker_ctx(None)
        eng = h_mod._get_engine("xiaohongshu", None, lambda *a: None)
        assert eng is not None
        assert eng.ctx is None  # degrades to pre-S9a behavior


# ─── L15: _do_qr_login real navigation + screenshot (degrades without ctx) ─

class TestDoQrLogin:
    async def test_without_ctx_returns_stub_path(self, tmp_data_dir):
        cap, out = _cap()
        h_mod.set_worker_ctx(None)
        res = await h_mod._do_qr_login("xiaohongshu", 7, cap)
        assert res is not None
        assert "qr_path" in res
        assert any(m == "qr_ready" for m, _ in out)

    async def test_with_ctx_navigates_and_screenshots(self, tmp_data_dir):
        page = _MockPage()
        h_mod.set_worker_ctx(_MockCtx(page))
        cap, out = _cap()
        res = await h_mod._do_qr_login("xiaohongshu", 8, cap)
        assert res is not None
        assert "qr_path" in res
        # Real navigation to the login URL + a screenshot were taken.
        assert page.goto_calls, "page.goto must be called when ctx is live"
        assert page.screenshot_calls == 1
        assert any(m == "qr_ready" for m, _ in out)

    async def test_with_ctx_screenshot_failure_degrades_to_stub(self, tmp_data_dir, monkeypatch):
        page = _MockPage()
        # Force screenshot to raise — handler must degrade, not crash.
        async def _boom(**kw):
            raise RuntimeError("disk full")
        page.screenshot = _boom
        h_mod.set_worker_ctx(_MockCtx(page))
        res = await h_mod._do_qr_login("xiaohongshu", 9, lambda *a: None)
        assert res is not None
        assert "qr_path" in res


# ─── L10: solver wiring in _handle_need_human ──────────────────────────────

def _anon_spec():
    """A platform spec that opts into auto-solve (anonymous + auto_then_manual)."""
    from semilabs_hone.modules.collection.scrapers.spec import (
        LoginSpec, PlatformSpec,
    )
    return PlatformSpec(
        platform="cargosite",
        display_name="Cargo",
        base_url="https://cargo.example.com",
        login=LoginSpec(type="qrcode", login_url="/login"),
        risk_tier="anonymous",
        captcha_policy="auto_then_manual",
    )


class TestSolverWiring:
    async def test_anonymous_auto_solve_success_skips_need_human(self, tmp_data_dir, monkeypatch):
        """anonymous+auto_then_manual + solver returns solved → 'resume', no need_human sink."""
        from semilabs_hone.modules.collection.risk_probes import ProbeHit

        # registry.get returns our anon spec; detect_and_solve returns solved.
        import semilabs_hone.modules.collection.scrapers.registry as reg
        monkeypatch.setattr(reg, "get", lambda p: (_anon_spec(), None))

        solved = {"status": "solved"}
        import semilabs_hone.modules.collection.captcha.solver as solver_mod
        async def _fake_solve(page, ctx, tier, policy):
            solved["_called"] = True
            class _R:
                status = "solved"
            return _R()
        monkeypatch.setattr(solver_mod, "detect_and_solve", _fake_solve)

        page = _MockPage()
        h_mod.set_worker_ctx(_MockCtx(page))

        cap, out = _cap()
        hit = ProbeHit(kind="captcha", platform="cargosite")
        result = await h_mod._handle_need_human("tid", "rid", hit, cap, 0)

        assert result == "resume"
        assert any(m == "captcha_solved" for m, _ in out)
        assert not any(m == "need_human" for m, _ in out)
        assert solved.get("_called") is True

    async def test_anonymous_auto_solve_failure_sinks_to_need_human(self, tmp_data_dir, monkeypatch):
        """anonymous+auto_then_manual but solver fails → sink to need_human (await_resume stubbed)."""
        from semilabs_hone.modules.collection.risk_probes import ProbeHit

        import semilabs_hone.modules.collection.scrapers.registry as reg
        monkeypatch.setattr(reg, "get", lambda p: (_anon_spec(), None))

        import semilabs_hone.modules.collection.captcha.solver as solver_mod
        async def _fail_solve(page, ctx, tier, policy):
            class _R:
                status = "paused"
            return _R()
        monkeypatch.setattr(solver_mod, "detect_and_solve", _fail_solve)

        page = _MockPage()
        h_mod.set_worker_ctx(_MockCtx(page))

        orig_await = h_mod._await_resume
        async def _noop_resume(rid, poll_interval=2.0):
            return "resume"
        h_mod._await_resume = _noop_resume
        cap, out = _cap()
        try:
            hit = ProbeHit(kind="captcha", platform="cargosite")
            result = await h_mod._handle_need_human("tid", "rid", hit, cap, 0)
            assert result == "resume"
            assert any(m == "need_human" for m, _ in out)
        finally:
            h_mod._await_resume = orig_await

    async def test_account_manual_skips_solver(self, tmp_data_dir, monkeypatch):
        """XHS (account/manual) → solver never called → straight need_human."""
        from semilabs_hone.modules.collection.risk_probes import ProbeHit

        # Real xiaohongshu registry entry is account/manual.
        called = {"n": 0}
        import semilabs_hone.modules.collection.captcha.solver as solver_mod
        async def _should_not_call(page, ctx, tier, policy):
            called["n"] += 1
            class _R:
                status = "solved"
            return _R()
        monkeypatch.setattr(solver_mod, "detect_and_solve", _should_not_call)

        h_mod.set_worker_ctx(_MockCtx(_MockPage()))

        orig_await = h_mod._await_resume
        async def _noop_resume(rid, poll_interval=2.0):
            return "resume"
        h_mod._await_resume = _noop_resume
        cap, out = _cap()
        try:
            hit = ProbeHit(kind="captcha", platform="xiaohongshu")
            await h_mod._handle_need_human("tid", "rid", hit, cap, 0)
            assert called["n"] == 0  # solver skipped for account/manual
            assert any(m == "need_human" for m, _ in out)
        finally:
            h_mod._await_resume = orig_await


# ─── _await_resume writes heartbeat while suspended (L01 viability) ────────

class TestAwaitResumeHeartbeat:
    async def test_heartbeat_refreshed_while_waiting(self, tmp_data_dir, monkeypatch):
        """While suspended awaiting resume, the worker refreshes heartbeat each poll."""
        import asyncio
        from semilabs_hone.core.ipc.paths import atomic_write_json, control_path

        beats = {"n": 0}
        import semilabs_hone.core.ipc.paths as paths
        orig = paths.write_heartbeat
        def _count(msg=None, **kw):
            beats["n"] += 1
        monkeypatch.setattr(paths, "write_heartbeat", _count)

        rid = "hb-rid"
        # Resume arrives after a short delay.
        async def _write_later():
            await asyncio.sleep(0.05)
            atomic_write_json(control_path(rid), {"action": "resume"})
        t = asyncio.create_task(_write_later())
        try:
            result = await h_mod._await_resume(rid, poll_interval=0.01)
            assert result == "resume"
        finally:
            await t
        # Heartbeat was written at least once while waiting.
        assert beats["n"] >= 1
