"""Collection browser worker entry point.

Design: docs/skim_design.md §1.1, §4, §6.4.

Flow:
    1. Read account_id from CLI args
    2. ensure_profile
    3. find_free_port + launch_real_chrome + attach
    4. Hook: inject stealth noise (DM-06, try/except no-op)
    5. Register handlers (DM-11, try/except empty table)
    6. Serve: core.ipc.server.serve_worker(module="collection", ...)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from semilabs_hone.modules.collection.browser.cdp import (
    CDPAttachError,
    attach,
    find_free_port,
    launch_real_chrome,
)
from semilabs_hone.modules.collection.browser.profile import ensure_profile


def main(argv: list[str] | None = None) -> int:
    """Entry point for the collection browser worker.

    Parses args, launches Chrome, attaches via CDP, and runs the IPC server loop.
    Returns exit code (0=ok, 1=error).
    """
    parser = argparse.ArgumentParser(description="Collection browser worker")
    parser.add_argument("--account", type=int, required=True, help="Account ID")
    args = parser.parse_args(argv)
    account_id = args.account

    logger.info(f"Starting collection worker for account {account_id}")

    # Ensure profile directory exists
    profile_dir = ensure_profile(account_id)
    logger.info(f"Profile dir: {profile_dir}")

    # Find free port, launch Chrome, and run async lifecycle
    try:
        port = find_free_port()
        logger.info(f"CDP port: {port}")

        proc = launch_real_chrome(str(profile_dir), port)
        logger.info(f"Chrome PID: {proc.pid}")

        asyncio.run(_run_worker(port))
    except KeyboardInterrupt:
        logger.info("Worker interrupted, shutting down")
    except CDPAttachError as exc:
        # PRD §8.1 场景 1.2: port busy / CDP connect refused. Surface the
        # exact user-facing hint. The worker exits; the web-side heartbeat
        # watchdog will reap the zombie `running` task → paused + WS within 30s.
        logger.error(f"CDP attach failed: {exc.fix_hint}")
        return 1
    except Exception as exc:
        logger.error(f"Worker failed: {exc}")
        return 1

    return 0


async def _run_worker(port: int) -> None:
    """Async lifecycle: attach, hooks, serve loop."""
    # Attach via CDP
    browser, ctx = await attach(port)
    logger.info("Attached to Chrome via CDP")

    # --- Hook: stealth noise injection (DM-06, not yet implemented) ---
    try:
        from semilabs_hone.modules.collection.anti_detect.stealth import inject_noise
        await inject_noise(ctx)
        logger.info("Stealth noise injected")
    except (ImportError, AttributeError):
        logger.debug("Stealth module not available, skipping noise injection")

    # --- Hook: handler registry (DM-11, not yet implemented) ---
    try:
        from semilabs_hone.modules.collection.handlers import build_registry
        handler_registry = build_registry()
    except (ImportError, AttributeError):
        logger.debug("Handlers module not available, using empty registry")
        handler_registry = {}

    # --- Serve IPC loop ---
    from semilabs_hone.core.ipc.server import serve_worker

    def on_progress(request_id: str, message: str, data: dict) -> None:
        logger.info(f"[progress] {request_id}: {message}")

    logger.info("Entering IPC serve loop")
    await serve_worker(
        module="collection",
        handler_registry=handler_registry,
        on_progress=on_progress,
    )
