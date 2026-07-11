"""Misc module coverage — warmup, manual_handler, ocr_solver, worker_main, cdp.

Covers the small uncovered surfaces: warmup.random_browse (both branches),
manual_handler.request_manual_solve (write/no-request_id), ocr_solver.solve_ocr
(ImportError/success/exception), worker_main._run_worker (lifecycle + ImportError
hooks), cdp._is_port_free/_is_own_chrome/attach success.
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── warmup.random_browse ─────────────────────────────────────────────────

class TestWarmupRandomBrowse:
    async def test_human_branch_calls_human_random_browse(self, monkeypatch):
        from semilabs_hone.modules.collection.scheduler import warmup
        from semilabs_hone.modules.collection.anti_detect import human_behavior

        calls = {"n": 0}

        async def fake_human_browse(page, timing):
            calls["n"] += 1

        monkeypatch.setattr(human_behavior, "random_browse", fake_human_browse)
        monkeypatch.setattr(warmup.random, "randint", lambda a, b: 3)
        monkeypatch.setattr(warmup.random, "sample", lambda pop, k: pop[:k])
        monkeypatch.setattr(warmup.asyncio, "sleep", AsyncMock())

        page = MagicMock()
        await warmup.random_browse(page)
        assert calls["n"] == 3  # one per selected URL

    async def test_fallback_branch_uses_page_goto(self, monkeypatch):
        from semilabs_hone.modules.collection.scheduler import warmup
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if "human_behavior" in name:
                raise ImportError("no module")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(warmup.random, "randint", lambda a, b: 2)
        monkeypatch.setattr(warmup.random, "sample", lambda pop, k: pop[:k])
        monkeypatch.setattr(warmup.asyncio, "sleep", AsyncMock())

        page = MagicMock()
        page.goto = AsyncMock()
        await warmup.random_browse(page)
        assert page.goto.call_count == 2

    async def test_warmup_failure_is_non_critical(self, monkeypatch):
        from semilabs_hone.modules.collection.scheduler import warmup
        from semilabs_hone.modules.collection.anti_detect import human_behavior

        async def failing(page, timing):
            raise RuntimeError("warmup page failed")
        monkeypatch.setattr(human_behavior, "random_browse", failing)
        monkeypatch.setattr(warmup.random, "randint", lambda a, b: 2)
        monkeypatch.setattr(warmup.random, "sample", lambda pop, k: pop[:k])
        monkeypatch.setattr(warmup.asyncio, "sleep", AsyncMock())

        # Must not raise.
        await warmup.random_browse(MagicMock())


# ─── manual_handler.request_manual_solve ──────────────────────────────────

class TestManualHandler:
    def _setup_ipc_results(self, tmp_data_dir, monkeypatch):
        import config
        results = tmp_data_dir / "ipc" / "results"
        results.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, "IPC_RESULTS", results)
        return results

    async def test_writes_paused_result_with_ws_events(self, tmp_data_dir, monkeypatch):
        results = self._setup_ipc_results(tmp_data_dir, monkeypatch)
        from semilabs_hone.modules.collection.captcha import manual_handler
        ctx = MagicMock()
        ctx.request_id = "req-manual-1"
        await manual_handler.request_manual_solve(ctx, "slide", 5)

        import json
        result_path = results / "req-manual-1.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["status"] == "paused"
        assert data["ws_events"][0]["type"] == "captcha_required"
        assert data["ws_events"][0]["data"]["captcha_type"] == "slide"

    async def test_no_request_id_no_file(self, tmp_data_dir, monkeypatch):
        results = self._setup_ipc_results(tmp_data_dir, monkeypatch)
        from semilabs_hone.modules.collection.captcha import manual_handler
        ctx = MagicMock()
        ctx.request_id = None
        await manual_handler.request_manual_solve(ctx, "ocr", 6)
        # No result file written.
        assert not (results / "None.json").exists()
        assert len(list(results.iterdir())) == 0


# ─── ocr_solver.solve_ocr ─────────────────────────────────────────────────

class TestOcrSolver:
    async def test_importerror_returns_empty(self):
        # ddddocr not installed in this env → ""
        from semilabs_hone.modules.collection.captcha.ocr_solver import solve_ocr
        assert await solve_ocr(b"img") == ""

    async def test_success_returns_text(self, monkeypatch):
        fake_ocr = MagicMock()
        fake_ocr.DdddOcr = MagicMock(return_value=MagicMock(
            classification=MagicMock(return_value="  AB12  ")))
        monkeypatch.setitem(sys.modules, "ddddocr", fake_ocr)

        from semilabs_hone.modules.collection.captcha.ocr_solver import solve_ocr
        text = await solve_ocr(b"img")
        assert text == "AB12"

    async def test_exception_returns_empty(self, monkeypatch):
        fake_ocr = MagicMock()
        fake_ocr.DdddOcr = MagicMock(return_value=MagicMock(
            classification=MagicMock(side_effect=RuntimeError("boom"))))
        monkeypatch.setitem(sys.modules, "ddddocr", fake_ocr)

        from semilabs_hone.modules.collection.captcha.ocr_solver import solve_ocr
        assert await solve_ocr(b"img") == ""


# ─── worker_main._run_worker lifecycle ────────────────────────────────────

class TestWorkerRunWorker:
    async def test_run_worker_invokes_lifecycle(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import worker_main

        async def fake_attach(port):
            return MagicMock(), MagicMock()

        async def fake_inject(ctx):
            return None

        monkeypatch.setattr(worker_main, "attach", fake_attach)
        monkeypatch.setattr(
            "semilabs_hone.modules.collection.anti_detect.stealth.inject_noise",
            fake_inject)

        served = {"called": False}

        async def fake_serve(module, handler_registry, on_progress=None):
            served["called"] = True

        monkeypatch.setattr(
            "semilabs_hone.core.ipc.server.serve_worker", fake_serve)

        await worker_main._run_worker(9333)
        assert served["called"] is True

    async def test_run_worker_stealth_importerror_skipped(self, monkeypatch):
        """Stealth/handler ImportError paths are caught (no crash)."""
        from semilabs_hone.modules.collection.browser import worker_main

        async def fake_attach(port):
            return MagicMock(), MagicMock()

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if "stealth" in name or "handlers" in name:
                raise ImportError("no module")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(worker_main, "attach", fake_attach)

        async def fake_serve(*a, **k):
            pass

        monkeypatch.setattr(
            "semilabs_hone.core.ipc.server.serve_worker", fake_serve)

        await worker_main._run_worker(9333)  # no raise


# ─── cdp helpers ──────────────────────────────────────────────────────────

class TestCdpHelpers:
    def test_is_port_free_true_when_bind_succeeds(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def bind(self, *a):
                return None

        monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
        assert cdp._is_port_free(9333) is True

    def test_is_port_free_false_on_oserror(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def bind(self, *a):
                raise OSError("in use")

        monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
        assert cdp._is_port_free(9333) is False

    def test_is_own_chrome_detects_chrome(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        result = MagicMock()
        result.stdout = "Google Chrome 1234\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
        assert cdp._is_own_chrome(9333) is True

    def test_is_own_chrome_no_chrome(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        result = MagicMock()
        result.stdout = "python 1234\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
        assert cdp._is_own_chrome(9333) is False

    def test_is_own_chrome_lsof_timeout(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="lsof", timeout=5)
        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert cdp._is_own_chrome(9333) is False

    def test_is_own_chrome_filenotfound(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        def raise_fnf(*a, **k):
            raise FileNotFoundError("lsof not installed")
        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert cdp._is_own_chrome(9333) is False

    async def test_attach_success_returns_browser_ctx(self, monkeypatch):
        from semilabs_hone.modules.collection.browser import cdp

        class _FakePW:
            class chromium:
                @staticmethod
                async def connect_over_cdp(endpoint):
                    browser = MagicMock()
                    browser.close = AsyncMock()
                    return browser

            async def start(self):
                return self

            async def stop(self):
                return None

        fake_mod = type(sys)("playwright.async_api")
        fake_mod.async_playwright = lambda: _FakePW()
        monkeypatch.setitem(sys.modules, "playwright.async_api", fake_mod)

        browser, ctx = await cdp.attach(9333)
        assert browser is not None
