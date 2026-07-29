"""Mock tests for DM-06 fingerprint module (FIX_PLAN F6 revision).

assign_fingerprint is a per-account random draw (no process-wide singleton,
no disk cache); load_fingerprint rebuilds from account fields incl. viewport;
apply_to_page goes through CDP Emulation overrides. No playwright required.
"""
from __future__ import annotations

import itertools
from unittest.mock import MagicMock

import pytest

from semilabs_hone.modules.collection.anti_detect import fingerprint as fp_mod
from semilabs_hone.modules.collection.anti_detect.fingerprint import (
    Fingerprint,
    apply_to_page,
    assign_fingerprint,
    load_fingerprint,
)


# ─── Fingerprint model tests ───────────────────────────────────────────────


class TestFingerprintModel:
    """Tests for the Fingerprint pydantic model."""

    def test_fingerprint_has_required_fields(self):
        """Fingerprint should have viewport, color_scheme, timezone, locale fields."""
        fp = Fingerprint(
            viewport={"width": 1280, "height": 720},
            color_scheme="light",
            timezone="Asia/Shanghai",
            locale="zh-CN",
        )
        assert fp.viewport == {"width": 1280, "height": 720}
        assert fp.color_scheme == "light"
        assert fp.timezone == "Asia/Shanghai"
        assert fp.locale == "zh-CN"

    def test_fingerprint_model_dump(self):
        """Fingerprint.model_dump() should return all fields."""
        fp = Fingerprint(
            viewport={"width": 1920, "height": 1080},
            color_scheme="dark",
            timezone="America/New_York",
            locale="en-US",
        )
        data = fp.model_dump()
        assert "viewport" in data
        assert "color_scheme" in data
        assert "timezone" in data
        assert "locale" in data


# ─── assign_fingerprint tests ───────────────────────────────────────────────


class TestAssignFingerprint:
    """assign_fingerprint draws a fresh random fingerprint per call."""

    def test_assign_fingerprint_returns_valid(self, tmp_data_dir):
        """assign_fingerprint should return a valid Fingerprint."""
        fp = assign_fingerprint()
        assert isinstance(fp, Fingerprint)
        assert "width" in fp.viewport
        assert "height" in fp.viewport
        assert fp.color_scheme in ("light", "dark")
        assert isinstance(fp.timezone, str) and "/" in fp.timezone
        assert isinstance(fp.locale, str) and "-" in fp.locale

    def test_assign_fingerprint_not_singleton(self, tmp_data_dir, monkeypatch):
        """Two accounts must get independent draws (no process-wide singleton)."""
        viewports = itertools.cycle(fp_mod._VIEWPORTS)
        timezones = itertools.cycle(fp_mod._TIMEZONES)

        def fake_choice(seq):
            if seq is fp_mod._VIEWPORTS:
                return next(viewports)
            if seq is fp_mod._TIMEZONES:
                return next(timezones)
            return seq[0]

        monkeypatch.setattr(fp_mod.random, "choice", fake_choice)
        fp1 = assign_fingerprint()
        fp2 = assign_fingerprint()
        assert fp1 != fp2
        assert fp1.viewport != fp2.viewport
        assert fp1.timezone != fp2.timezone

    def test_assign_fingerprint_writes_no_file(self, tmp_data_dir):
        """The old global assigned_fingerprint.json cache must be gone."""
        assign_fingerprint()
        stale = tmp_data_dir / "collection" / "assigned_fingerprint.json"
        assert not stale.exists()


# ─── load_fingerprint tests ─────────────────────────────────────────────────


class TestLoadFingerprint:
    """load_fingerprint rebuilds the fingerprint purely from account fields."""

    def test_load_fingerprint_from_dict(self, tmp_data_dir):
        """load_fingerprint should read all fields from dict, incl. viewport."""
        account = {
            "viewport_w": 1366,
            "viewport_h": 768,
            "color_scheme": "dark",
            "timezone": "America/New_York",
            "locale": "en-US",
        }
        fp = load_fingerprint(account)
        assert fp.viewport == {"width": 1366, "height": 768}
        assert fp.color_scheme == "dark"
        assert fp.timezone == "America/New_York"
        assert fp.locale == "en-US"

    def test_load_fingerprint_from_object(self, tmp_data_dir):
        """load_fingerprint should read from object attributes."""
        account = MagicMock()
        account.viewport_w = 1440
        account.viewport_h = 900
        account.color_scheme = "dark"
        account.timezone = "Europe/London"
        account.locale = "en-GB"
        fp = load_fingerprint(account)
        assert fp.viewport == {"width": 1440, "height": 900}
        assert fp.color_scheme == "dark"
        assert fp.timezone == "Europe/London"
        assert fp.locale == "en-GB"

    def test_load_fingerprint_defaults(self, tmp_data_dir):
        """load_fingerprint should use accounts-table defaults when missing."""
        fp = load_fingerprint("unknown")
        assert fp.viewport == {"width": 1920, "height": 1080}
        assert fp.color_scheme == "light"
        assert fp.timezone == "Asia/Shanghai"
        assert fp.locale == "zh-CN"

    def test_load_fingerprint_does_not_draw_random(self, tmp_data_dir, monkeypatch):
        """load_fingerprint must never call assign_fingerprint (no randomness)."""
        monkeypatch.setattr(
            fp_mod, "assign_fingerprint",
            lambda: pytest.fail("load_fingerprint must not draw randomly"),
        )
        fp = load_fingerprint({"color_scheme": "dark"})
        assert fp.color_scheme == "dark"


# ─── apply_to_page tests ────────────────────────────────────────────────────


class _FakeCDPSession:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict):
        self.sent.append((method, params))


class _FakePage:
    def __init__(self):
        self.session = _FakeCDPSession()
        self.context = MagicMock()
        self.context.new_cdp_session = self._new_cdp_session

    async def _new_cdp_session(self, page):
        assert page is self
        return self.session


class TestApplyToPage:
    """apply_to_page applies fingerprint via CDP Emulation overrides."""

    @pytest.mark.asyncio
    async def test_apply_to_page_sends_cdp_emulation(self, tmp_data_dir):
        """timezone/locale/color-scheme go through Emulation.* CDP methods."""
        page = _FakePage()
        fp = Fingerprint(
            viewport={"width": 1366, "height": 768},
            color_scheme="dark",
            timezone="Asia/Tokyo",
            locale="ja-JP",
        )
        await apply_to_page(page, fp)

        sent = dict(page.session.sent)
        assert sent["Emulation.setTimezoneOverride"] == {"timezoneId": "Asia/Tokyo"}
        assert sent["Emulation.setLocaleOverride"] == {"locale": "ja-JP"}
        assert sent["Emulation.setEmulatedMedia"] == {
            "features": [{"name": "color-scheme", "value": "dark"}]
        }

    @pytest.mark.asyncio
    async def test_apply_to_page_does_not_override_viewport(self, tmp_data_dir):
        """Viewport must NOT be overridden — real window is used (design §4.3)."""
        page = _FakePage()
        fp = Fingerprint(
            viewport={"width": 1280, "height": 720},
            color_scheme="light",
            timezone="Asia/Shanghai",
            locale="zh-CN",
        )
        await apply_to_page(page, fp)
        methods = [m for m, _ in page.session.sent]
        assert "Emulation.setDeviceMetricsOverride" not in methods
