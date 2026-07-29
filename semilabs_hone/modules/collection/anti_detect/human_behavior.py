"""Layer 4 — human-like interaction primitives.

- human_type: per-character delay 50-200ms with 5% long pause
- human_click: Bezier-curve mouse + random offset
- random_scroll: natural scroll pattern
- random_browse: warmup page navigation
- generate_slide_track: accelerate-then-decelerate + overshoot rebound

All functions use lazy playwright import so this module is importable
without playwright installed.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Locator as PWLocator
    from playwright.async_api import Page


def _resolve_locator(page: "Page", locator: dict[str, Any]) -> "PWLocator":
    """Resolve a multi-strategy locator dict to a Playwright Locator.

    Priority: text > aria_label > role > css > nth fallback.
    """
    if "text" in locator and locator["text"]:
        return page.get_by_text(str(locator["text"]))
    if "aria_label" in locator and locator["aria_label"]:
        return page.get_by_label(str(locator["aria_label"]))
    if "role" in locator and locator["role"]:
        return page.get_by_role(str(locator["role"]))
    if "css" in locator and locator["css"]:
        return page.locator(str(locator["css"]))
    # nth fallback — grab the nth element of a generic selector
    nth = locator.get("nth", 1)
    return page.locator(f"body > *:nth-of-type({nth})")


async def human_type(page: "Page", locator: dict, text: str) -> None:
    """Type text character by character with 50-200ms delay, 5% long pause."""
    element = _resolve_locator(page, locator)
    await element.click()  # focus the element first

    for i, ch in enumerate(text):
        await element.press(ch)
        # 5% chance of a long pause (500-1500ms) to simulate thinking
        if random.random() < 0.05:
            delay = random.uniform(0.5, 1.5)
        else:
            delay = random.uniform(0.05, 0.2)  # 50-200ms
        await asyncio.sleep(delay)


async def human_click(page: "Page", locator: dict) -> None:
    """Click with Bezier-curve mouse movement + random offset."""
    element = _resolve_locator(page, locator)
    box = await element.bounding_box()
    if not box:
        await element.click()
        return

    # Random offset within the element (avoid edges)
    margin = 0.2
    x = box["x"] + box["width"] * random.uniform(margin, 1 - margin)
    y = box["y"] + box["height"] * random.uniform(margin, 1 - margin)

    # Move to element with a Bezier-like path
    await _move_mouse_bezier(page, x, y)
    await page.mouse.click(x, y)


async def _move_mouse_bezier(page: "Page", target_x: float, target_y: float, steps: int = 20) -> None:
    """Move mouse along a quadratic Bezier curve with random control point."""
    # Current mouse position (default to 0,0)
    sx, sy = 0.0, 0.0  # playwright tracks internally; just dispatch moves

    # Random control point to create a natural arc
    cx = target_x * 0.5 + random.uniform(-80, 80)
    cy = target_y * 0.3 + random.uniform(-60, 60)

    for t_frac in range(1, steps + 1):
        t = t_frac / steps
        # Quadratic Bezier: B(t) = (1-t)^2*P0 + 2(1-t)t*C + t^2*P1
        px = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * target_x
        py = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * target_y
        await page.mouse.move(px, py)
        # Variable speed: slower near start and end, faster in middle
        speed = 0.01 + 0.03 * math.sin(math.pi * t)
        await asyncio.sleep(speed)


async def random_scroll(page: "Page", max_times: int, wait_ms: int) -> None:
    """Scroll page randomly up to max_times, each wait_ms apart.

    Uses physical mouse.wheel with multi-step small deltas + micro-pauses
    (PRD §4.2.1 human-scroll redline). Forbids page.evaluate("window.scrollBy")
    instant teleportation — that synthesizes a trusted-less event with no
    pointer trail and is a strong machine signal.
    """
    for _ in range(random.randint(1, max_times)):
        # Split the scroll into several small wheel deltas with tiny pauses,
        # simulating a finger flick / mouse wheel roll.
        total = random.randint(300, 900)
        steps = random.randint(3, 6)
        per = max(1, total // steps)
        for _ in range(steps):
            await page.mouse.wheel(0, per)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        jitter = random.uniform(-0.3, 0.3) * wait_ms
        await asyncio.sleep(max(0.1, (wait_ms + jitter) / 1000))


async def smart_wait(page: "Page", selector: str, timeout: float = 5000) -> None:
    """Wait for an element to be ready, then add a human reaction delay.

    PRD §4.2.1 smart-wait redline: never bare time.sleep(5). Must first ensure
    the element exists via wait_for_selector, THEN stack a random
    1.5-3.5s human reaction latency on top.
    """
    await page.wait_for_selector(selector, timeout=timeout)
    await asyncio.sleep(random.uniform(1.5, 3.5))


async def random_browse(page: "Page", pages: tuple[int, int]) -> None:
    """Warmup: browse a random number of pages within the given range."""
    count = random.randint(pages[0], pages[1])
    # Typical warmup URLs — in practice these come from a config list
    warmup_urls = [
        "https://www.google.com",
        "https://www.bing.com",
        "https://news.ycombinator.com",
        "https://en.wikipedia.org",
    ]
    for _ in range(count):
        url = random.choice(warmup_urls)
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 8))
        except Exception:
            # Non-critical: warmup failures are silently skipped
            pass


def generate_slide_track(distance: float) -> list[dict]:
    """Generate a physical slider track: accelerate then decelerate + overshoot rebound.

    Returns a list of {x, y, t} dicts representing the track points.
    """
    total_steps = random.randint(30, 60)
    track: list[dict] = []
    start_time = 0.0

    for i in range(total_steps):
        t = i / total_steps
        # Ease-in-out: accelerate first half, decelerate second half
        if t < 0.5:
            progress = 2 * t * t  # ease in
        else:
            progress = 1 - 2 * (1 - t) * (1 - t)  # ease out

        x = distance * progress
        # Slight Y jitter to simulate hand tremor
        y = math.sin(i * 0.5) * random.uniform(1, 5)
        elapsed = (i / total_steps) * random.uniform(200, 500)

        track.append({"x": round(x, 2), "y": round(y, 2), "t": round(elapsed, 2)})

    # Overshoot: go past the target then rebound
    overshoot = distance * random.uniform(0.02, 0.08)
    rebound_steps = random.randint(3, 8)
    base_time = track[-1]["t"] if track else 0
    for j in range(rebound_steps):
        t = j / rebound_steps
        decay = (1 - t) ** 2  # exponential decay
        ox = distance + overshoot * decay * math.cos(j * math.pi)
        oy = math.sin(j * 1.2) * random.uniform(0.5, 2) * decay
        ot = base_time + (j + 1) * random.uniform(10, 30)
        track.append({"x": round(ox, 2), "y": round(oy, 2), "t": round(ot, 2)})

    return track
