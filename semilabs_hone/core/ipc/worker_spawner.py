"""Web-side on-demand worker spawner (L13).

CLAUDE.local.md: the collection browser worker is pulled up on demand by the
web process via subprocess.Popen (CDP port 9333-9340). Previously
`manifest.WORKER_ENTRY` was only registered but never launched, so IPC request
files written by `api_create_task` / `api_login_account` were never consumed and
tasks hung in `pending` forever (S8 探查 L13).

The spawner is best-effort: it checks the worker heartbeat before spawning
(skip when a worker is already alive), wraps Popen in try/except so a launch
failure never breaks the HTTP request (the heartbeat watchdog reaps the zombie
`running` task within 30s). Only attached to `app.state` when
`config.WORKER_AUTOSPAWN` is truthy — tests build `create_app()` with it off, so
route handlers skip spawning entirely (no real Chrome in CI).
"""
from __future__ import annotations

import subprocess
import sys
from typing import Callable

from loguru import logger

# MVP single browser: one live worker proc at a time. Keyed by account_id so a
# task for a different account re-spawns (replacing the prior handle); the prior
# Chrome is torn down by its own worker exit (worker_main._run_worker finally).
_procs: dict[int, subprocess.Popen] = {}

# Reuse the watchdog's staleness threshold: a heartbeat fresher than this means
# a worker is already alive and serving the IPC bus.
_FRESH_THRESHOLD = 30.0


def _heartbeat_fresh() -> bool:
    """True if a worker heartbeat was written within the freshness threshold."""
    try:
        from semilabs_hone.core.ipc.paths import heartbeat_age
        age = heartbeat_age()
    except Exception:
        return False
    return age is not None and age < _FRESH_THRESHOLD


def make_default_spawner() -> Callable[[int], None]:
    """Return a spawner(account_id) that Popen's the collection worker on demand.

    Idempotent within the freshness window: a fresh heartbeat → no-op. Failures
    are logged, never raised (request returns; watchdog reaps zombie later).
    """

    def _spawn(account_id: int) -> None:
        if account_id is None:
            return
        if _heartbeat_fresh():
            logger.debug(f"[spawner] worker heartbeat fresh, skipping spawn for account {account_id}")
            return
        # Reuse an existing handle if it is still alive (poll() None == running).
        prev = _procs.get(account_id)
        if prev is not None and prev.poll() is None:
            logger.debug(f"[spawner] worker for account {account_id} already running (pid={prev.pid})")
            return
        try:
            cmd = [
                sys.executable, "-m",
                "semilabs_hone.modules.collection.browser.worker_main",
                "--account", str(account_id),
            ]
            proc = subprocess.Popen(cmd, start_new_session=True)
            _procs[account_id] = proc
            logger.info(f"[spawner] launched collection worker for account {account_id} (pid={proc.pid})")
        except Exception as exc:
            # Don't break the request — the heartbeat watchdog will reap the
            # zombie `running` task → paused + WS within 30s.
            logger.error(f"[spawner] failed to spawn worker for account {account_id}: {exc}")

    return _spawn
