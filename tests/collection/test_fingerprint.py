"""Mock tests for DM-06 fingerprint module.

Tests Fingerprint model, assign_fingerprint fixedness, load_fingerprint,
and apply_fingerprint behavior. No playwright required.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from semilabs_hone.modules.collection.anti_detect.fingerprint import (
    Fingerprint,
    assign_fingerprint,
    load_fingerprint,
    reset_assigned,
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
    """Tests for assign_fingerprint."""

    def setup_method(self):
        """Reset assigned fingerprint before each test."""
        reset_assigned()

    def test_assign_fingerprint_returns_valid(self, tmp_data_dir):
        """assign_fingerprint should return a valid Fingerprint."""
        fp = assign_fingerprint()
        assert isinstance(fp, Fingerprint)
        assert "width" in fp.viewport
        assert "height" in fp.viewport
        assert fp.color_scheme in ("light", "dark")
        assert isinstance(fp.timezone, str) and "/" in fp.timezone
        assert isinstance(fp.locale, str) and "-" in fp.locale

    def test_assign_fingerprint_fixedness(self, tmp_data_dir):
        """assign_fingerprint called twice should return the same fingerprint."""
        fp1 = assign_fingerprint()
        fp2 = assign_fingerprint()
        assert fp1 == fp2
        assert fp1.viewport == fp2.viewport
        assert fp1.color_scheme == fp2.color_scheme
        assert fp1.timezone == fp2.timezone
        assert fp1.locale == fp2.locale

    def test_assign_fingerprint_persists(self, tmp_data_dir):
        """assign_fingerprint should persist to disk and reload."""
        fp1 = assign_fingerprint()
        # Reset in-memory cache
        reset_assigned()
        # Reload should read from disk and return same values
        fp2 = assign_fingerprint()
        assert fp1.viewport == fp2.viewport
        assert fp1.color_scheme == fp2.color_scheme
        assert fp1.timezone == fp2.timezone
        assert fp1.locale == fp2.locale


# ─── load_fingerprint tests ─────────────────────────────────────────────────


class TestLoadFingerprint:
    """Tests for load_fingerprint."""

    def setup_method(self):
        reset_assigned()

    def test_load_fingerprint_from_dict(self, tmp_data_dir):
        """load_fingerprint should read color_scheme/timezone/locale from dict."""
        account = {
            "color_scheme": "dark",
            "timezone": "America/New_York",
            "locale": "en-US",
        }
        fp = load_fingerprint(account)
        assert fp.color_scheme == "dark"
        assert fp.timezone == "America/New_York"
        assert fp.locale == "en-US"

    def test_load_fingerprint_from_object(self, tmp_data_dir):
        """load_fingerprint should read from object attributes."""
        account = MagicMock()
        account.color_scheme = "dark"
        account.timezone = "Europe/London"
        account.locale = "en-GB"
        fp = load_fingerprint(account)
        assert fp.color_scheme == "dark"
        assert fp.timezone == "Europe/London"
        assert fp.locale == "en-GB"

    def test_load_fingerprint_defaults(self, tmp_data_dir):
        """load_fingerprint should use defaults for unknown account types."""
        reset_assigned()
        fp = load_fingerprint("unknown")
        assert fp.color_scheme == "light"
        assert fp.timezone == "Asia/Shanghai"
        assert fp.locale == "zh-CN"

    def test_load_fingerprint_uses_assigned_viewport(self, tmp_data_dir):
        """load_fingerprint should use the assigned fingerprint's viewport."""
        reset_assigned()
        fp_assigned = assign_fingerprint()
        account = {"color_scheme": "dark", "timezone": "Tokyo", "locale": "ja-JP"}
        fp_loaded = load_fingerprint(account)
        assert fp_loaded.viewport == fp_assigned.viewport
