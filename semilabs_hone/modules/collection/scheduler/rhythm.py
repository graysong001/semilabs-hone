"""Rhythm scheduling for collection worker.

Anti-detection Layer 6: quiet hours, daily limits, random delays, captcha pause threshold.
Pure stdlib (time/datetime/random) — no heavy deps.
Design: docs/skim_design.md §12
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, time as dtime

import config
from semilabs_hone.core.utils.retry import DailyLimitError, QuietHoursError


def check_quiet_hours(now: datetime | None = None) -> None:
    """Check if current time is within quiet hours (22:00-07:00).

    Args:
        now: Current time (defaults to now). Useful for testing.

    Raises:
        QuietHoursError: If current hour is within the quiet period.
    """
    if now is None:
        now = datetime.now()

    hour = now.hour
    quiet_start, quiet_end = config.QUIET_HOURS  # (22, 7)

    # Quiet hours span midnight: 22:00-07:00
    # Inside if hour >= 22 OR hour < 7
    if hour >= quiet_start or hour < quiet_end:
        raise QuietHoursError(
            f"Quiet hours active ({quiet_start}:00-{quiet_end}:00), current hour: {hour}"
        )


def check_daily_limit(account: object) -> None:
    """Check if account has reached the daily scrape limit.

    Args:
        account: Account object with daily_scrape_count attribute (or dict).

    Raises:
        DailyLimitError: If account's daily count >= DAILY_LIMIT_PER_ACCOUNT.
    """
    limit = config.DAILY_LIMIT_PER_ACCOUNT  # 200

    # Support both object attribute and dict access
    if isinstance(account, dict):
        count = account.get("daily_scrape_count", 0)
    else:
        count = getattr(account, "daily_scrape_count", 0)

    if count >= limit:
        raise DailyLimitError(
            f"Daily limit reached: {count}/{limit}"
        )


async def note_delay() -> None:
    """Random delay between notes (30-90 seconds)."""
    low, high = config.NOTE_DELAY  # (30, 90)
    delay = random.uniform(low, high)
    await asyncio.sleep(delay)


async def keyword_delay() -> None:
    """Random delay between keywords (60-180 seconds)."""
    low, high = config.KEYWORD_DELAY  # (60, 180)
    delay = random.uniform(low, high)
    await asyncio.sleep(delay)


def should_pause_for_captcha(fail_count: int) -> bool:
    """Determine if worker should pause due to captcha failures.

    Core principle: fail once -> pause (账号比脚本值钱).

    Args:
        fail_count: Number of consecutive captcha failures.

    Returns:
        True if fail_count >= 1.
    """
    return fail_count >= 1
