"""Mock tests for DM-05 collection browser: cdp, profile, worker_main.

Uses monkeypatch to mock subprocess + socket port detection.
No real Chrome or playwright required.
"""
from __future__ import annotations

import subprocess
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Lazy/conditional imports — cdp.py lazy-imports playwright so module-level
# import is safe without playwright installed.
from semilabs_hone.modules.collection.browser import cdp, profile


# ─── launch_real_chrome tests ───────────────────────────────────────────────


class TestLaunchRealChrome:
    """Tests for cdp.launch_real_chrome."""

    def test_launch_real_chrome_happy_path_calls_popen(self, tmp_path):
        """launch_real_chrome should call subprocess.Popen with correct binary and two flags."""
        mock_popen = MagicMock(return_value=MagicMock(pid=12345))
        prof = str(tmp_path / "profiles" / "1")

        with patch.object(cdp.subprocess, "Popen", mock_popen):
            result = cdp.launch_real_chrome(prof, 9333)

        # Called exactly once
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        # First arg is Chrome binary
        assert "Google Chrome" in args[0]
        # Only two flags
        flags = [a for a in args if a.startswith("--")]
        assert len(flags) == 2
        assert any("--remote-debugging-port=" in f for f in flags)
        assert any("--user-data-dir=" in f for f in flags)
        # Verify no forbidden flags
        flag_str = " ".join(args)
        assert "enable-automation" not in flag_str
        assert "AutomationControlled" not in flag_str
        assert "no-sandbox" not in flag_str

    def test_launch_real_chrome_no_extra_flags(self, tmp_path):
        """Chrome args must contain ONLY whitelisted flags."""
        mock_popen = MagicMock(return_value=MagicMock(pid=1))
        prof = str(tmp_path / "profiles" / "1")

        with patch.object(cdp.subprocess, "Popen", mock_popen):
            cdp.launch_real_chrome(prof, 9333)

        args = mock_popen.call_args[0][0]
        # Exactly 3 elements: binary + 2 flags
        assert len(args) == 3


# ─── find_free_port tests ──────────────────────────────────────────────────


class TestFindFreePort:
    """Tests for cdp.find_free_port."""

    def test_find_free_port_returns_first_free_in_range(self, monkeypatch):
        """When port 9333 is free, find_free_port returns it."""
        def fake_is_free(port):
            return True

        with patch.object(cdp, "_is_port_free", fake_is_free):
            result = cdp.find_free_port()
        assert result == 9333

    def test_find_free_port_increments_when_occupied(self, monkeypatch):
        """When lower ports are occupied, find_free_port returns the next free."""
        occupied = {9333, 9334, 9335}

        def fake_is_free(port):
            return port not in occupied

        with patch.object(cdp, "_is_port_free", fake_is_free):
            result = cdp.find_free_port()
        assert result == 9336

    def test_find_free_port_reuses_own_chrome_port(self, monkeypatch):
        """If own Chrome occupies a port, reuse it instead of incrementing."""
        def fake_is_free(port):
            return False  # All ports occupied

        def fake_is_own(port):
            return port == 9334  # Only 9334 is our own Chrome

        with patch.object(cdp, "_is_port_free", fake_is_free):
            with patch.object(cdp, "_is_own_chrome", fake_is_own):
                result = cdp.find_free_port()
        assert result == 9334

    def test_find_free_port_all_occupied_not_own(self, monkeypatch):
        """When all range ports are occupied by other programs, return next free after range."""
        def fake_is_free(port):
            # All ports up to 9345 occupied, 9346 free
            return port >= 9346

        def fake_is_own(port):
            return False  # None is our own

        with patch.object(cdp, "_is_port_free", fake_is_free):
            with patch.object(cdp, "_is_own_chrome", fake_is_own):
                result = cdp.find_free_port()
        assert result == 9346


# ─── CDPAttachError tests (PRD §8.1 场景 1.2) ──────────────────────────────


class TestCDPAttachError:
    """attach() must classify connect_over_cdp failures as CDPAttachError."""

    def test_cdpattach_error_carries_hint(self):
        """CDPAttachError carries the PRD §8.1 user-facing hint."""
        err = cdp.CDPAttachError()
        assert cdp.CDP_PORT_BUSY_HINT in str(err)
        assert err.fix_hint == cdp.CDP_PORT_BUSY_HINT

    @pytest.mark.asyncio
    async def test_attach_wraps_connection_failure(self):
        """connect_over_cdp failure → CDPAttachError with the busy-port hint."""
        import asyncio

        class _FakePW:
            class chromium:
                @staticmethod
                async def connect_over_cdp(endpoint):
                    raise ConnectionError("connect ECONNREFUSED")

            async def start(self):
                return self

        # Stub async_playwright so attach does not need real playwright.
        import sys
        fake_pw_mod = type(sys)("playwright.async_api")
        fake_pw_mod.async_playwright = lambda: _FakePW()
        with patch.dict(sys.modules, {"playwright.async_api": fake_pw_mod}):
            with pytest.raises(cdp.CDPAttachError) as exc_info:
                await cdp.attach(9333)
        assert cdp.CDP_PORT_BUSY_HINT in str(exc_info.value)


class TestWorkerMainCDPAttachFailure:
    """worker_main must exit 1 on CDPAttachError (PRD §8.1 场景 1.2)."""

    def test_main_exits_on_cdp_attach_error(self, tmp_data_dir, monkeypatch):
        """A CDPAttachError from attach surfaces as exit code 1."""
        from semilabs_hone.modules.collection.browser import worker_main

        async def fake_attach(port):
            raise cdp.CDPAttachError()

        monkeypatch.setattr(worker_main, "find_free_port", lambda: 9333)
        monkeypatch.setattr(
            worker_main, "launch_real_chrome",
            lambda pd, p: MagicMock(pid=1),
        )
        monkeypatch.setattr(
            worker_main, "ensure_profile",
            lambda aid: tmp_data_dir / "collection" / "profiles" / str(aid),
        )
        monkeypatch.setattr(worker_main, "attach", fake_attach)

        rc = worker_main.main(["--account", "1"])
        assert rc == 1



# ─── profile tests ──────────────────────────────────────────────────────────


class TestProfile:
    """Tests for profile.py."""

    def test_profile_dir_for_correct_path(self, tmp_data_dir):
        """profile_dir_for should return data/collection/profiles/<id>/."""
        p = profile.profile_dir_for(42)
        assert p == tmp_data_dir / "collection" / "profiles" / "42"

    def test_ensure_profile_creates_directory(self, tmp_data_dir):
        """ensure_profile should create the directory."""
        target = tmp_data_dir / "collection" / "profiles" / "7"
        assert not target.exists()
        result = profile.ensure_profile(7)
        assert result == target
        assert target.exists()
        assert target.is_dir()

    def test_ensure_profile_idempotent(self, tmp_data_dir):
        """ensure_profile called twice should not error."""
        r1 = profile.ensure_profile(10)
        r2 = profile.ensure_profile(10)
        assert r1 == r2


# ─── worker_main graceful failure tests ─────────────────────────────────────


class TestWorkerMainGracefulFailure:
    """Tests for worker_main.main failing gracefully without Chrome."""

    def test_main_graceful_failure_no_chrome(self, tmp_data_dir, monkeypatch):
        """main should not crash when Chrome binary does not exist."""
        from semilabs_hone.modules.collection.browser import worker_main

        def fake_launch(profile_dir, port):
            raise FileNotFoundError("Chrome not found")

        monkeypatch.setattr(worker_main, "find_free_port", lambda: 9333)
        monkeypatch.setattr(worker_main, "launch_real_chrome", fake_launch)
        monkeypatch.setattr(worker_main, "ensure_profile", lambda aid: tmp_data_dir / "collection" / "profiles" / str(aid))

        rc = worker_main.main(["--account", "1"])
        assert rc == 1

    def test_main_exits_on_attach_failure(self, tmp_data_dir, monkeypatch):
        """main should not crash when attach fails."""
        from semilabs_hone.modules.collection.browser import worker_main

        async def fake_attach(port):
            raise ConnectionError("CDP connection refused")

        monkeypatch.setattr(worker_main, "find_free_port", lambda: 9333)
        monkeypatch.setattr(worker_main, "launch_real_chrome", lambda pd, p: MagicMock(pid=1))
        monkeypatch.setattr(worker_main, "ensure_profile", lambda aid: tmp_data_dir / "collection" / "profiles" / str(aid))
        monkeypatch.setattr(worker_main, "attach", fake_attach)

        rc = worker_main.main(["--account", "1"])
        assert rc == 1
