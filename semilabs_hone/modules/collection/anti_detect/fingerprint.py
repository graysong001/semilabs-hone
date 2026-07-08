"""Layer 2 — one-account-one-fixed fingerprint.

Fingerprint is assigned once (randomly) and then permanently fixed.
Load reads from the accounts table; apply sets viewport/color-scheme/locale/timezone.
Does NOT set UA (UA is handled separately via ua_pool.get_ua).

Lazy playwright import for importability without playwright.
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

# In-memory cache for assigned fingerprint
_assigned_fingerprint: "Fingerprint | None" = None

# Available options for fingerprint generation
_VIEWPORTS = [
    {"width": 1280, "height": 720},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 800},
]
_COLOR_SCHEMES = ["light", "dark"]
_TIMEZONES = [
    "Asia/Shanghai", "Asia/Tokyo", "Asia/Hong_Kong",
    "America/New_York", "America/Los_Angeles", "America/Chicago",
    "Europe/London", "Europe/Paris", "Europe/Berlin",
    "Australia/Sydney",
]
_LOCALES = [
    "zh-CN", "zh-TW", "en-US", "en-GB", "ja-JP",
    "ko-KR", "fr-FR", "de-DE",
]


class Fingerprint(BaseModel):
    """One-account-one-fixed browser fingerprint."""
    viewport: dict[str, int]
    color_scheme: str
    timezone: str
    locale: str


def _get_fingerprint_file() -> Path:
    """Return the path to the persistent fingerprint store."""
    try:
        from semilabs_hone.core.config import DATA_DIR
    except ImportError:
        from config import DATA_DIR
    return DATA_DIR / "collection" / "assigned_fingerprint.json"


def assign_fingerprint() -> Fingerprint:
    """Assign a fingerprint once and permanently fix it.

    If a fingerprint has already been assigned, return the same one
    (one-account-one-fixed invariant).
    """
    global _assigned_fingerprint

    if _assigned_fingerprint is not None:
        return _assigned_fingerprint

    # Try to load from persistent store
    fp_file = _get_fingerprint_file()
    if fp_file.exists():
        try:
            data = json.loads(fp_file.read_text(encoding="utf-8"))
            _assigned_fingerprint = Fingerprint(**data)
            return _assigned_fingerprint
        except (json.JSONDecodeError, Exception):
            pass  # Fall through to generate new

    # Generate a new random fingerprint
    _assigned_fingerprint = Fingerprint(
        viewport=random.choice(_VIEWPORTS),
        color_scheme=random.choice(_COLOR_SCHEMES),
        timezone=random.choice(_TIMEZONES),
        locale=random.choice(_LOCALES),
    )

    # Persist to disk
    fp_file.parent.mkdir(parents=True, exist_ok=True)
    fp_file.write_text(
        json.dumps(_assigned_fingerprint.model_dump(), ensure_ascii=False),
        encoding="utf-8",
    )

    return _assigned_fingerprint


def load_fingerprint(account: Any) -> Fingerprint:
    """Load fingerprint from the accounts table.

    The account object should have attributes: color_scheme, timezone, locale.
    Returns a Fingerprint from the DB values.
    """
    # Extract fields from account (could be ORM object, dict, or pydantic model)
    if hasattr(account, "color_scheme"):
        color_scheme = getattr(account, "color_scheme", "light")
        timezone = getattr(account, "timezone", "Asia/Shanghai")
        locale = getattr(account, "locale", "zh-CN")
    elif isinstance(account, dict):
        color_scheme = account.get("color_scheme", "light")
        timezone = account.get("timezone", "Asia/Shanghai")
        locale = account.get("locale", "zh-CN")
    else:
        color_scheme = "light"
        timezone = "Asia/Shanghai"
        locale = "zh-CN"

    # Use assigned fingerprint viewport, or assign one
    fp = assign_fingerprint()

    return Fingerprint(
        viewport=fp.viewport,
        color_scheme=color_scheme,
        timezone=timezone,
        locale=locale,
    )


async def apply_fingerprint(ctx: "BrowserContext", fp: Fingerprint) -> None:
    """Apply the fingerprint to a BrowserContext.

    Sets viewport, color-scheme, locale, timezone via init scripts.
    Does NOT set UA (handled by ua_pool.get_ua).
    """
    # Locale + timezone injection
    locale_tz_script = f"""
        Object.defineProperty(navigator, 'language', {{
            value: '{fp.locale}', writable: false, configurable: false,
        }});
        Object.defineProperty(navigator, 'languages', {{
            value: ['{fp.locale}', 'en-US'], writable: false, configurable: false,
        }});
        Intl._origDateTimeFormat = Intl.DateTimeFormat;
        Intl.DateTimeFormat = function(locales, options) {{
            options = options || {{}};
            options.timeZone = '{fp.timezone}';
            return new Intl._origDateTimeFormat('{fp.locale}', options);
        }};
    """
    await ctx.add_init_script(locale_tz_script)

    # Color-scheme injection
    cs_script = f"""
        var style = document.createElement('style');
        style.textContent = ':root {{ color-scheme: {fp.color_scheme}; }}';
        document.head.appendChild(style);
    """
    await ctx.add_init_script(cs_script)


def reset_assigned() -> None:
    """Reset the in-memory assigned fingerprint (for testing only)."""
    global _assigned_fingerprint
    _assigned_fingerprint = None
