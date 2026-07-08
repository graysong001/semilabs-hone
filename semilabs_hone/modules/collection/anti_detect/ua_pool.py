"""UA pool — real Chrome UA by default, variety from remote cache optionally.

config.UA_STRATEGY=="real" (default): page.evaluate("navigator.userAgent")
config.UA_STRATEGY=="variety": fetch from remote + cache in data/collection/ua_pool.json
  (TTL 24h), filter by local Chrome major version. Bundled static list for offline fallback.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

# Bundled static UA list for offline fallback (macOS Chrome only)
_BUNDLED_UAS: list[str] = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_CHROME_MAJOR_RE = re.compile(r"Chrome/(\d+)\.")


def _get_chrome_major_version(ua: str) -> int | None:
    """Extract Chrome major version from a UA string."""
    m = _CHROME_MAJOR_RE.search(ua)
    return int(m.group(1)) if m else None


def _get_pool_file() -> Path:
    try:
        from semilabs_hone.core.config import DATA_DIR
    except ImportError:
        from config import DATA_DIR
    return DATA_DIR / "collection" / "ua_pool.json"


async def get_ua(ctx: "BrowserContext", account: Any = None) -> str:
    """Get UA for an account.

    Strategy depends on config.UA_STRATEGY:
    - "real" (default): read navigator.userAgent from the browser, no spoofing
    - "variety": fetch from remote URL, cache, filter by Chrome major version
    """
    try:
        from semilabs_hone.core import config
    except ImportError:
        import config  # type: ignore

    strategy = getattr(config, "UA_STRATEGY", "real")

    if strategy == "real":
        return await _get_real_ua(ctx)

    # variety strategy
    return await _get_variety_ua(ctx)


async def _get_real_ua(ctx: "BrowserContext") -> str:
    """Read the real navigator.userAgent from the browser."""
    page = await ctx.new_page()
    try:
        ua = await page.evaluate("navigator.userAgent")
        return ua
    finally:
        await page.close()


async def _get_variety_ua(ctx: "BrowserContext") -> str:
    """Get UA from remote pool with caching and Chrome version filtering."""
    try:
        from semilabs_hone.core import config
    except ImportError:
        import config  # type: ignore

    # First, get real Chrome major version from the browser
    real_ua = await _get_real_ua(ctx)
    chrome_major = _get_chrome_major_version(real_ua)

    # Try to load from cache
    pool_file = _get_pool_file()
    pool_data = _load_pool_cache(pool_file)

    if pool_data and chrome_major:
        # Filter by matching Chrome major version
        matching = [
            ua for ua in pool_data.get("uas", [])
            if _get_chrome_major_version(ua) == chrome_major
        ]
        if matching:
            return matching[0]  # Return first match

    # Try to fetch from remote
    remote_url = getattr(config, "UA_REMOTE_URL", None)
    if remote_url:
        try:
            uas = await _fetch_remote_uas(remote_url)
            if chrome_major:
                matching = [
                    ua for ua in uas
                    if _get_chrome_major_version(ua) == chrome_major
                ]
                if matching:
                    # Save to cache
                    _save_pool_cache(pool_file, uas)
                    return matching[0]
        except Exception:
            pass  # Fall through to bundled

    # Offline fallback: bundled static list with stale marker
    if chrome_major:
        matching = [
            ua for ua in _BUNDLED_UAS
            if _get_chrome_major_version(ua) == chrome_major
        ]
        if matching:
            return matching[0]

    # Last resort: return real UA
    return real_ua


def _load_pool_cache(pool_file: Path) -> dict | None:
    """Load cached UA pool if TTL hasn't expired."""
    try:
        from semilabs_hone.core import config
    except ImportError:
        import config  # type: ignore

    ttl = getattr(config, "UA_POOL_TTL", 86400)

    if not pool_file.exists():
        return None

    try:
        data = json.loads(pool_file.read_text(encoding="utf-8"))
        if time.time() - data.get("fetched_at", 0) > ttl:
            return None  # Expired
        return data
    except (json.JSONDecodeError, Exception):
        return None


def _save_pool_cache(pool_file: Path, uas: list[str]) -> None:
    """Save UA pool to cache file."""
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text(
        json.dumps({"uas": uas, "fetched_at": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


async def _fetch_remote_uas(url: str) -> list[str]:
    """Fetch UA list from remote endpoint.

    Expected format: JSON array of UA strings, or newline-separated text.
    """
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        # Try JSON first
        try:
            data = resp.json()
            if isinstance(data, list):
                return [str(u) for u in data if u]
            if isinstance(data, dict) and "uas" in data:
                return [str(u) for u in data["uas"] if u]
        except Exception:
            pass

        # Fallback: newline-separated
        return [line.strip() for line in resp.text.splitlines() if line.strip()]
