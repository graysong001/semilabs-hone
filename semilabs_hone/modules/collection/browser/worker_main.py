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
import signal
import sys
from types import FrameType

from loguru import logger

from semilabs_hone.modules.collection.browser.cdp import (
    CDPAttachError,
    attach,
    find_free_port,
    launch_real_chrome,
)
from semilabs_hone.modules.collection.browser.profile import ensure_profile


def _install_signal_handlers() -> None:
    """Turn SIGTERM/SIGINT into a normal unwind.

    The spawner stops workers with SIGTERM; without this the process
    dies before its ``finally`` block and leaves the real Chrome running
    (USER_SOP G30).
    """
    def _graceful(signum: int, _frame: FrameType | None) -> None:
        logger.info(f"Worker received signal {signum}, shutting down")
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _graceful)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the collection browser worker.

    Parses args, launches Chrome, attaches via CDP, and runs the IPC server loop.
    Returns exit code (0=ok, 1=error).
    """
    parser = argparse.ArgumentParser(description="Collection browser worker")
    parser.add_argument("--account", type=int, required=True, help="Account ID")
    args = parser.parse_args(argv)
    account_id = args.account

    _install_signal_handlers()
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

        try:
            asyncio.run(_run_worker(port, account_id))
        finally:
            # G30/F11: idle exit / crash / SIGTERM must not leak the real
            # Chrome process (browser.close() inside _run_worker detaches the
            # CDP session; terminate() reaps the process itself).
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
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


async def _run_worker(port: int, account_id: int) -> None:
    """Async lifecycle: attach, hooks, resource injection, serve loop."""
    # Attach via CDP
    browser, ctx = await attach(port)
    logger.info("Attached to Chrome via CDP")

    # --- Load bound account from DB (for fingerprint / handler resources) ---
    account = _load_account(account_id)

    # L14/F1: publish the live BrowserContext + bound account to the handlers
    # module so the handler-built GenericEngine (and _do_qr_login / solver
    # wiring) can resolve a page instead of raising "No page available".
    try:
        from semilabs_hone.modules.collection.handlers import set_worker_resources
        set_worker_resources(ctx, account)
    except Exception:
        logger.debug("Could not publish worker resources to handlers (engine page will be None)")

    try:
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

        logger.info("Entering IPC serve loop (idle exit enabled, F11)")
        await serve_worker(
            module="collection",
            handler_registry=handler_registry,
            on_progress=on_progress,
            account_id=account_id,
            # idle_timeout default: config.WORKER_IDLE_TIMEOUT
        )
    finally:
        # Tear down the browser we launched so a worker exit does not leak a
        # Chrome process holding the CDP port (next worker's find_free_port
        # would otherwise skip past a zombie). Also reap any Chrome the
        # handlers relaunched after the user closed the original window.
        try:
            from semilabs_hone.modules.collection import handlers as _handlers_mod
            _handlers_mod.terminate_relaunched_chromes()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass


def _load_account(account_id: int):
    """Load the bound Account row (detached) for resource injection."""
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account

        sess = get_session()
        try:
            account = sess.query(Account).filter(Account.id == account_id).first()
            if account is not None:
                sess.expunge(account)  # detach: safe to use after session close
            return account
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to load account {account_id}: {exc}")
        return None


if __name__ == "__main__":
    # Entry point for `python -m semilabs_hone.modules.collection.browser.worker_main`,
    # which is exactly how the spawner launches this worker (USER_SOP G31).
    sys.exit(main())
