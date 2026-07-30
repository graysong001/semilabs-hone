"""Layer 2 — one-account-one-fixed fingerprint.

A fingerprint is drawn randomly once per account at account-creation time and
persisted on the accounts table (viewport_w/h, color_scheme, timezone, locale).
load_fingerprint rebuilds it from the account row; apply_to_page applies it
per page via CDP Emulation overrides (CDP attach mode, design §4.3).

Does NOT set UA (read from the real Chrome at runtime, §5.3) and does NOT
override viewport (CDP attach mode keeps the real window size, by design).
"""
from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel

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
# 地理一致性收敛 (2026-07-30): 时区/locale 必须与真实出口 IP 地理一致 ——
# 本机中国 IP 下抽中"纽约时区+fr-FR"这类组合会触发时区-IP 交叉校验风控,
# 属于自伤行为。收敛到真实中国用户最大众的值(与 viewport/UA "不伪造"
# 哲学自洽: 人群中最普通即最好的隐藏)。账号间独立性由 viewport/
# color_scheme 维度保留。
_TIMEZONES = ["Asia/Shanghai"]
_LOCALES = ["zh-CN"]


class Fingerprint(BaseModel):
    """One-account-one-fixed browser fingerprint."""
    viewport: dict[str, int]
    color_scheme: str
    timezone: str
    locale: str


def assign_fingerprint() -> Fingerprint:
    """Draw a new random fingerprint.

    Called once per account at creation time; the result is persisted on the
    accounts table. Never a process-wide singleton — every account gets its
    own independent draw (shared fingerprints across accounts invite
    correlation bans).
    """
    return Fingerprint(
        viewport=random.choice(_VIEWPORTS),
        color_scheme=random.choice(_COLOR_SCHEMES),
        timezone=random.choice(_TIMEZONES),
        locale=random.choice(_LOCALES),
    )


def load_fingerprint(account: Any) -> Fingerprint:
    """Rebuild the fixed fingerprint from an account row or dict.

    Reads viewport_w/viewport_h/color_scheme/timezone/locale; missing fields
    fall back to the accounts-table defaults.
    """
    if isinstance(account, dict):
        getter = account.get
    else:
        def getter(key: str, default: Any = None) -> Any:
            return getattr(account, key, default)

    viewport_w = getter("viewport_w", 1920) or 1920
    viewport_h = getter("viewport_h", 1080) or 1080
    return Fingerprint(
        viewport={"width": viewport_w, "height": viewport_h},
        color_scheme=getter("color_scheme", "light") or "light",
        timezone=getter("timezone", "Asia/Shanghai") or "Asia/Shanghai",
        locale=getter("locale", "zh-CN") or "zh-CN",
    )


async def apply_to_page(page: Any, fp: Fingerprint) -> None:
    """Apply the fingerprint to a page via CDP Emulation overrides.

    CDP attach mode (design §4.3): viewport uses the real window and is NOT
    overridden; timezone/locale/color-scheme are applied per page through a
    CDP session so no init-script tampering shows up in the page.
    """
    session = await page.context.new_cdp_session(page)
    await session.send("Emulation.setTimezoneOverride", {"timezoneId": fp.timezone})
    await session.send("Emulation.setLocaleOverride", {"locale": fp.locale})
    await session.send(
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "color-scheme", "value": fp.color_scheme}]},
    )
