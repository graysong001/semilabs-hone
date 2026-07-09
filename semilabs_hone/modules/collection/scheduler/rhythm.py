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


def is_quiet_hours(now: datetime | None = None) -> bool:
    """Whether the current local time is within quiet hours (22:00-07:00).

    Pure predicate (PRD §4.5.1/§7.4). The main loop calls this before each
    request; when True it should enter night-sleep via sleep_until_wakeup()
    rather than throw-and-retry.
    """
    if now is None:
        now = datetime.now()
    quiet_start, quiet_end = config.QUIET_HOURS  # (22, 7)
    return now.hour >= quiet_start or now.hour < quiet_end


def seconds_until_wakeup(now: datetime | None = None) -> float:
    """Seconds from now until the next 07:00 local time boundary.

    Quiet window is 22:00-07:00; wakeup is 07:00. If called outside quiet
    hours returns 0. Deterministic & testable — no wall-clock side effects.
    """
    if now is None:
        now = datetime.now()
    quiet_end = config.QUIET_HOURS[1]  # 7
    wakeup = now.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    if not is_quiet_hours(now):
        return 0.0
    # If now is before wakeup today (e.g. 03:00), wakeup is today 07:00.
    # If now is after wakeup today but still quiet, that can't happen since
    # quiet window is 22:00-07:00; >=22 means wakeup is next day 07:00.
    if wakeup <= now:
        # Push to next day
        from datetime import timedelta

        wakeup = wakeup + timedelta(days=1)
    return (wakeup - now).total_seconds()


async def sleep_until_wakeup(now: datetime | None = None) -> float:
    """Long asyncio.sleep until 07:00 — PRD night-sleep mechanism.

    Does NOT exit the worker process and issues NO network requests during
    22:00-07:00 (PRD §7.4). Returns the seconds slept (for logging/tests).
    """
    secs = seconds_until_wakeup(now)
    if secs > 0:
        await asyncio.sleep(secs)
    return secs


def check_quiet_hours(now: datetime | None = None) -> None:
    """Guard: raise QuietHoursError if within quiet hours.

    Kept as a hard guard for call sites that must refuse to issue a request
    during 22:00-07:00. The main loop prefers is_quiet_hours()+sleep_until_wakeup()
    (PRD long-sleep mechanism); this guard remains for defense-in-depth.
    """
    if is_quiet_hours(now):
        if now is None:
            now = datetime.now()
        quiet_start, quiet_end = config.QUIET_HOURS  # (22, 7)
        raise QuietHoursError(
            f"Quiet hours active ({quiet_start}:00-{quiet_end}:00), current hour: {now.hour}"
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
