"""Warmup scheduler for collection worker.

Anti-detection Layer 6: browse 2-5 unrelated pages before scraping,
30-90s each. Uses DM-06 human_behavior.random_browse.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import config


async def random_browse(page: Any) -> None:
    """Browse 2-5 unrelated warmup pages, 30-90s each.

    This delegates to DM-06 human_behavior.random_browse for the
    actual page navigation, wrapping it with warmup-appropriate timing.

    Args:
        page: Playwright Page object.
    """
    try:
        from semilabs_hone.modules.collection.anti_detect.human_behavior import (
            random_browse as _human_random_browse,
        )

        num_pages = random.randint(*config.WARMUP_PAGES)  # 2-5

        # Warmup URLs — typical unrelated sites
        warmup_urls = [
            "https://www.google.com",
            "https://www.bing.com",
            "https://news.ycombinator.com",
            "https://en.wikipedia.org",
            "https://www.bbc.com",
            "https://www.reddit.com",
        ]

        selected = random.sample(warmup_urls, min(num_pages, len(warmup_urls)))

        for url in selected:
            try:
                await _human_random_browse(page, (num_pages, num_pages))
            except Exception:
                # Warmup failures are non-critical
                pass

            # Each warmup page: 30-90s dwell time
            dwell = random.uniform(30, 90)
            await asyncio.sleep(dwell)

    except ImportError:
        # Fallback if human_behavior is not available
        # Direct page navigation with human-like timing
        num_pages = random.randint(*config.WARMUP_PAGES)
        warmup_urls = [
            "https://www.google.com",
            "https://www.bing.com",
            "https://news.ycombinator.com",
            "https://en.wikipedia.org",
            "https://www.bbc.com",
            "https://www.reddit.com",
        ]
        selected = random.sample(warmup_urls, min(num_pages, len(warmup_urls)))

        for url in selected:
            try:
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            except Exception:
                pass
            dwell = random.uniform(30, 90)
            await asyncio.sleep(dwell)
