"""Layer 3 — stealth injection (DISABLED per PRD zero-injection redline).

PRD (docs/semilabs_hone_skim_sepc.md §7.1) forbids ALL script injection under
CDP-takeover mode: the real Chrome already has genuine navigator/WebGL/Canvas
values, and any add_init_script only creates a detectable anomaly. We rely on
"extremely slow human rhythm" to defeat risk control, NOT on forged params.

This module is retained as a no-op seam so callers (worker_main) and contract
tests keep importing `inject_noise` without breaking — it now does nothing.
The former Canvas/Audio noise script was removed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

# Kept as an empty sentinel for backward-compat imports; NO script is injected.
NOISE_ONLY_SCRIPT: str = ""


async def inject_noise(ctx: "BrowserContext") -> None:
    """No-op. Script injection is forbidden by the PRD anti-detection redline.

    Real Chrome via connect_over_cdp already exposes genuine fingerprints; we
    deliberately inject nothing. Kept as a callable seam for callers/tests.
    """
    logger.debug("stealth injection disabled (PRD zero-injection redline); real Chrome values used as-is")
    return None
