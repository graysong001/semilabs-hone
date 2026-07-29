"""Warmup browsing for the collection worker.

Anti-detection Layer 6 (design §12): before scraping, browse a couple of
unrelated pages with human-like dwell time, so the session does not start
with a cold profile jumping straight at the target platform.

Timing goes through ``rhythm.warmup_dwell`` — the single place where sleep
lengths live, so config drives production and tests can neutralise it.
``config.WARMUP_PAGES`` set to ``None``/``(0, 0)`` disables warmup.
"""
from __future__ import annotations

import random
from typing import Any, Sequence

import config
from loguru import logger

from semilabs_hone.modules.collection.scheduler import rhythm

#: Neutral, always-up sites used as warmup traffic.
DEFAULT_WARMUP_URLS = (
    "https://www.bing.com",
    "https://en.wikipedia.org",
    "https://news.ycombinator.com",
    "https://www.bbc.com",
)


def _pages_to_browse() -> int:
    """How many warmup pages this run should visit (0 = warmup disabled)."""
    window = getattr(config, "WARMUP_PAGES", None)
    if not window:
        return 0
    low, high = window
    if high <= 0:
        return 0
    return random.randint(max(0, low), high)


async def random_browse(page: Any, urls: Sequence[str] | None = None) -> None:
    """Visit a few unrelated pages on `page`, dwelling like a human.

    Navigation failures are non-critical: warmup is camouflage, never a
    reason to fail the task it precedes.
    """
    count = _pages_to_browse()
    if count == 0:
        return

    pool = list(urls or DEFAULT_WARMUP_URLS)
    for url in random.sample(pool, min(count, len(pool))):
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await _human_dwell(page)
        except Exception as exc:
            logger.debug(f"warmup navigation to {url} failed: {exc}")
        await rhythm.warmup_dwell()


async def _human_dwell(page: Any) -> None:
    """Scroll a little, like a person skimming the page."""
    try:
        from semilabs_hone.modules.collection.anti_detect.human_behavior import random_scroll

        await random_scroll(page, max_times=random.randint(1, 3), wait_ms=800)
    except ImportError:
        pass
