"""Risk-control probes — detect anti-bot interception after each browser action.

PRD §4.4.1: after every page.goto / list scroll / click-into-detail the worker
MUST immediately run a risk probe. On any hit the handler breaks the scrape
loop, sinks the task to ``need_human``, and waits for a human relay (PRD
§4.4.2/§4.4.3).

Detection is DOM/URL based and deliberately defensive: any exception while
probing is swallowed and treated as "no hit" so a flaky selector never aborts
a scrape (the scrape continues; the probe just misses once). A missed probe is
recoverable on the next action's probe.

Platform probes (PRD §4.4.1):
- Xiaohongshu: captcha/verify shield popup (class contains ``captcha`` /
  ``verify-slider`` / full-screen mask).
- Zhihu: forced redirect to ``/signin`` or an on-page login wall.
- Generic: a "scan-to-login" QR appears (session dropped mid-task).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

# DOM selectors that indicate an XHS risk-control interstitial.
XHS_CAPTCHA_SELECTORS = [
    "captcha-verify-container",
    ".captcha--slider",
    ".geetest_panel",
    '[class*="captcha"]',
    '[class*="verify-slider"]',
    '[class*="mask-layer"]',
]

# Selectors indicating a scan-to-login QR (generic session drop).
QR_LOGIN_SELECTORS = [
    '[class*="qrcode"]',
    'img[src*="qrcode"]',
    '[class*="login-scan"]',
]


@dataclass
class ProbeHit:
    """A positive risk-probe result.

    ``kind`` is one of: ``captcha`` | ``login_expired`` | ``qr_login``. The
    handler maps this to the IPC ``need_human`` status + a human-readable
    stage/message for the UI.
    """

    kind: str
    platform: str | None = None
    detail: str | None = None


async def probe(page: Any, platform: str = "xiaohongshu") -> ProbeHit | None:
    """Run the platform risk probe; return a ``ProbeHit`` on a hit, else None.

    Never raises — detection failures are logged and treated as no-hit so the
    scrape keeps going (PRD §4.3.1 fallback redline extends to probes).
    """
    try:
        # --- URL-based checks (cheap, no DOM query) ---
        url = ""
        try:
            url = page.url if hasattr(page, "url") else ""
        except Exception:
            url = ""

        # Zhihu: redirect to /signin
        if platform == "zhihu" and url and "/signin" in url:
            return ProbeHit(kind="login_expired", platform=platform,
                            detail=f"redirected to {url}")

        # Generic XHS-style: any login/signin redirect mid-task
        if url and _looks_like_login_redirect(url, platform):
            return ProbeHit(kind="login_expired", platform=platform,
                            detail=f"redirected to {url}")
    except Exception as exc:
        logger.debug(f"probe url-check failed: {exc}")

    # --- DOM-based checks ---
    try:
        if platform == "xiaohongshu":
            if await _any_selector(page, XHS_CAPTCHA_SELECTORS):
                return ProbeHit(kind="captcha", platform=platform)
        # Zhihu login wall DOM
        if platform == "zhihu":
            if await _any_selector(page, ['[class*="sign-in"]', '[class*="Login"]']):
                return ProbeHit(kind="login_expired", platform=platform)
        # Generic scan-to-login QR (any platform)
        if await _any_selector(page, QR_LOGIN_SELECTORS):
            return ProbeHit(kind="qr_login", platform=platform)
    except Exception as exc:
        logger.debug(f"probe dom-check failed: {exc}")

    return None


def _looks_like_login_redirect(url: str, platform: str) -> bool:
    """Heuristic: a mid-scrape redirect into a login path signals session drop."""
    lowered = url.lower()
    markers = ("/login", "/signin", "/passport", "login-page")
    return any(m in lowered for m in markers)


async def _any_selector(page: Any, selectors: list[str]) -> bool:
    """Return True if any selector matches an element on the page."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el is not None:
                return True
        except Exception:
            continue
    return False
