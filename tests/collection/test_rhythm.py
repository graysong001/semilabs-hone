"""Rhythm scheduling unit tests.

Pure logic tests for check_quiet_hours, check_daily_limit, note_delay,
keyword_delay, and should_pause_for_captcha.
Naming: test_<method>_<scenario>_<expected>.
Uses tmp_data_dir isolation to avoid touching real data.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from semilabs_hone.core.utils.retry import DailyLimitError, QuietHoursError
from semilabs_hone.modules.collection.scheduler.rhythm import (
    check_daily_limit,
    check_quiet_hours,
    is_quiet_hours,
    keyword_delay,
    note_delay,
    seconds_until_wakeup,
    should_pause_for_captcha,
    sleep_until_wakeup,
)


# ─── check_quiet_hours ────────────────────────────────────────────────────────


class TestCheckQuietHours:
    """Tests for check_quiet_hours."""

    def test_check_quiet_hours_at_22_raises(self):
        """22:00 is within quiet hours (22:00-07:00) → QuietHoursError."""
        now = datetime(2026, 1, 1, 22, 0, 0)
        with pytest.raises(QuietHoursError):
            check_quiet_hours(now=now)

    def test_check_quiet_hours_at_23_raises(self):
        """23:00 is within quiet hours → QuietHoursError."""
        now = datetime(2026, 1, 1, 23, 30, 0)
        with pytest.raises(QuietHoursError):
            check_quiet_hours(now=now)

    def test_check_quiet_hours_at_03_raises(self):
        """03:00 is within quiet hours → QuietHoursError."""
        now = datetime(2026, 1, 1, 3, 0, 0)
        with pytest.raises(QuietHoursError):
            check_quiet_hours(now=now)

    def test_check_quiet_hours_at_06_raises(self):
        """06:00 is within quiet hours → QuietHoursError."""
        now = datetime(2026, 1, 1, 6, 59, 0)
        with pytest.raises(QuietHoursError):
            check_quiet_hours(now=now)

    def test_check_quiet_hours_at_07_passes(self):
        """07:00 is at the boundary (exclusive end) → no error."""
        now = datetime(2026, 1, 1, 7, 0, 0)
        check_quiet_hours(now=now)  # Should not raise

    def test_check_quiet_hours_at_08_passes(self):
        """08:00 is outside quiet hours → no error."""
        now = datetime(2026, 1, 1, 8, 0, 0)
        check_quiet_hours(now=now)  # Should not raise

    def test_check_quiet_hours_at_12_passes(self):
        """12:00 (noon) is outside quiet hours → no error."""
        now = datetime(2026, 1, 1, 12, 0, 0)
        check_quiet_hours(now=now)  # Should not raise

    def test_check_quiet_hours_at_21_passes(self):
        """21:00 is just before quiet hours → no error."""
        now = datetime(2026, 1, 1, 21, 59, 0)
        check_quiet_hours(now=now)  # Should not raise


# ─── is_quiet_hours (PRD §4.5.1/§7.4 night-sleep predicate) ────────────────


class TestIsQuietHours:
    """Tests for is_quiet_hours predicate."""

    def test_is_quiet_hours_at_23_true(self):
        assert is_quiet_hours(datetime(2026, 1, 1, 23, 0, 0)) is True

    def test_is_quiet_hours_at_03_true(self):
        assert is_quiet_hours(datetime(2026, 1, 1, 3, 0, 0)) is True

    def test_is_quiet_hours_at_12_false(self):
        assert is_quiet_hours(datetime(2026, 1, 1, 12, 0, 0)) is False

    def test_is_quiet_hours_at_07_false(self):
        """07:00 boundary is wakeup time → not quiet."""
        assert is_quiet_hours(datetime(2026, 1, 1, 7, 0, 0)) is False

    def test_is_quiet_hours_at_22_true(self):
        assert is_quiet_hours(datetime(2026, 1, 1, 22, 0, 0)) is True


# ─── seconds_until_wakeup / sleep_until_wakeup ─────────────────────────────


class TestNightSleep:
    """PRD §4.5.1: worker must sleep until 07:00, not throw-and-retry."""

    def test_seconds_until_wakeup_before_dawn(self):
        """03:00 → 4 hours = 14400s until 07:00."""
        secs = seconds_until_wakeup(datetime(2026, 1, 1, 3, 0, 0))
        assert secs == 4 * 3600

    def test_seconds_until_wakeup_just_after_dusk(self):
        """22:30 → 8.5 hours until next 07:00."""
        secs = seconds_until_wakeup(datetime(2026, 1, 1, 22, 30, 0))
        assert secs == 8.5 * 3600

    def test_seconds_until_wakeup_outside_quiet_zero(self):
        """Noon → 0s (not in quiet hours)."""
        assert seconds_until_wakeup(datetime(2026, 1, 1, 12, 0, 0)) == 0.0

    @pytest.mark.asyncio
    async def test_sleep_until_wakeup_sleeps_until_dawn(self, monkeypatch):
        """sleep_until_wakeup must asyncio.sleep for the computed seconds."""
        slept = []

        async def fake_sleep(secs):
            slept.append(secs)

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep", fake_sleep
        )
        now = datetime(2026, 1, 1, 3, 0, 0)
        ret = await sleep_until_wakeup(now)
        assert ret == 4 * 3600
        assert slept == [4 * 3600]

    @pytest.mark.asyncio
    async def test_sleep_until_wakeup_no_sleep_outside_quiet(self, monkeypatch):
        """Outside quiet hours, sleep_until_wakeup must NOT sleep (PRD: no idle blocking)."""
        called = []

        async def fake_sleep(secs):
            called.append(secs)

        monkeypatch.setattr(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep", fake_sleep
        )
        ret = await sleep_until_wakeup(datetime(2026, 1, 1, 12, 0, 0))
        assert ret == 0.0
        assert called == []


# ─── check_daily_limit ───────────────────────────────────────────────────────


class TestCheckDailyLimit:
    """Tests for check_daily_limit."""

    def test_check_daily_limit_under_limit_passes(self):
        """daily_scrape_count=150 < 200 → no error."""
        account = {"daily_scrape_count": 150}
        check_daily_limit(account)  # Should not raise

    def test_check_daily_limit_at_limit_raises(self):
        """daily_scrape_count=200 == 200 → DailyLimitError."""
        account = {"daily_scrape_count": 200}
        with pytest.raises(DailyLimitError):
            check_daily_limit(account)

    def test_check_daily_limit_over_limit_raises(self):
        """daily_scrape_count=250 > 200 → DailyLimitError."""
        account = {"daily_scrape_count": 250}
        with pytest.raises(DailyLimitError):
            check_daily_limit(account)

    def test_check_daily_limit_zero_passes(self):
        """daily_scrape_count=0 → no error."""
        account = {"daily_scrape_count": 0}
        check_daily_limit(account)  # Should not raise

    def test_check_daily_limit_object_attribute(self):
        """Account as object with daily_scrape_count attribute."""

        class Account:
            daily_scrape_count = 201

        with pytest.raises(DailyLimitError):
            check_daily_limit(Account())

    def test_check_daily_limit_object_attribute_under(self):
        """Account as object under limit."""

        class Account:
            daily_scrape_count = 50

        check_daily_limit(Account())  # Should not raise


# ─── note_delay ──────────────────────────────────────────────────────────────


class TestNoteDelay:
    """Tests for note_delay."""

    async def test_note_delay_sleep_in_range(self):
        """note_delay should sleep between 30-90 seconds."""
        with patch(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            for _ in range(20):
                with patch(
                    "semilabs_hone.modules.collection.scheduler.rhythm.random.uniform",
                    return_value=45.0,
                ):
                    await note_delay()
                    mock_sleep.assert_called_once_with(45.0)
                    mock_sleep.reset_mock()

    async def test_note_delay_calls_random_uniform_in_config_range(self):
        """note_delay should call random.uniform with config.NOTE_DELAY bounds."""
        with patch(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with patch(
                "semilabs_hone.modules.collection.scheduler.rhythm.random.uniform",
                return_value=60.0,
            ) as mock_uniform:
                await note_delay()
                # random.uniform(low, high) where low=30, high=90
                mock_uniform.assert_called_once()
                args = mock_uniform.call_args[0]
                assert args[0] == 30
                assert args[1] == 90


# ─── keyword_delay ───────────────────────────────────────────────────────────


class TestKeywordDelay:
    """Tests for keyword_delay."""

    async def test_keyword_delay_sleep_in_range(self):
        """keyword_delay should sleep between 60-180 seconds."""
        with patch(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            with patch(
                "semilabs_hone.modules.collection.scheduler.rhythm.random.uniform",
                return_value=120.0,
            ):
                await keyword_delay()
                mock_sleep.assert_called_once_with(120.0)

    async def test_keyword_delay_calls_random_uniform_in_config_range(self):
        """keyword_delay should call random.uniform with config.KEYWORD_DELAY bounds."""
        with patch(
            "semilabs_hone.modules.collection.scheduler.rhythm.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with patch(
                "semilabs_hone.modules.collection.scheduler.rhythm.random.uniform",
                return_value=100.0,
            ) as mock_uniform:
                await keyword_delay()
                mock_uniform.assert_called_once()
                args = mock_uniform.call_args[0]
                assert args[0] == 60
                assert args[1] == 180


# ─── should_pause_for_captcha ────────────────────────────────────────────────


class TestShouldPauseForCaptcha:
    """Tests for should_pause_for_captcha."""

    def test_should_pause_for_captcha_zero_returns_false(self):
        """fail_count=0 → no pause."""
        assert should_pause_for_captcha(0) is False

    def test_should_pause_for_captcha_one_returns_true(self):
        """fail_count=1 → pause (core principle: fail once)."""
        assert should_pause_for_captcha(1) is True

    def test_should_pause_for_captcha_three_returns_true(self):
        """fail_count=3 → pause."""
        assert should_pause_for_captcha(3) is True

    def test_should_pause_for_captcha_five_returns_true(self):
        """fail_count=5 → pause (would also trigger account suspended in handler)."""
        assert should_pause_for_captcha(5) is True
