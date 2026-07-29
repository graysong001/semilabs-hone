"""Web-side on-demand worker spawner (L13 + FIX_PLAN F2 capabilities).

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

Merged supervisor capabilities (FIX_PLAN F2, merge plan §4.4):
- per-(module, account_id) process registry with dead-entry reaping;
- worker command resolved from the module manifest's ``WORKER_ENTRY``;
- worker stdout/stderr appended to ``data/logs/worker_<module>_<id>.log``
  with ``-u`` + ``PYTHONUNBUFFERED=1`` so a SIGTERM'd worker keeps its log
  tail (USER_SOP G33);
- ``stop_worker`` / ``shutdown_all`` for the app shutdown hook (G12: web exit
  must not leave orphan Chrome).
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from loguru import logger

# Live worker registry keyed by (module, account_id). A dead entry is reaped
# and relaunched on the next ensure_worker/spawner call; the prior Chrome is
# torn down by its own worker exit (worker_main finally + proc.terminate()).
_WORKERS: dict[tuple[str, int], subprocess.Popen] = {}

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


def _worker_entry(module: str) -> str:
    """Read WORKER_ENTRY from the module manifest."""
    manifest = importlib.import_module(f"semilabs_hone.modules.{module}.manifest")
    entry = getattr(manifest, "WORKER_ENTRY", None)
    if not entry:
        raise ValueError(f"module '{module}' manifest has no WORKER_ENTRY")
    return entry


def _log_path(module: str, account_id: int) -> Path:
    import config

    d = config.DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"worker_{module}_{account_id}.log"


def ensure_worker(module: str, account_id: int) -> subprocess.Popen:
    """Return the live worker for (module, account_id), launching if needed.

    A dead registry entry is reaped and relaunched automatically.
    """
    key = (module, account_id)
    proc = _WORKERS.get(key)
    if proc is not None:
        if proc.poll() is None:
            return proc
        logger.warning(
            f"worker {key} exited unexpectedly rc={proc.returncode}, relaunching"
        )
        _WORKERS.pop(key, None)

    import config

    entry = _worker_entry(module)
    cmd = [sys.executable, "-u", "-m", entry, "--account", str(account_id)]
    # -u / PYTHONUNBUFFERED: a worker killed mid-run must not lose its log
    # tail to stdio buffering (USER_SOP G33).
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # Parent may close its copy right after Popen (child keeps the dup'd fd).
    logf = open(_log_path(module, account_id), "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.REPO_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    finally:
        logf.close()
    _WORKERS[key] = proc
    logger.info(f"worker {key} launched pid={proc.pid}: {' '.join(cmd)}")
    return proc


def is_alive(module: str, account_id: int) -> bool:
    """True if the registered worker for (module, account_id) is running."""
    proc = _WORKERS.get((module, account_id))
    return proc is not None and proc.poll() is None


def stop_worker(module: str, account_id: int) -> None:
    """Terminate the worker for (module, account_id) if registered."""
    key = (module, account_id)
    proc = _WORKERS.pop(key, None)
    if proc is not None and proc.poll() is None:
        logger.info(f"stopping worker {key} pid={proc.pid}")
        proc.terminate()


def shutdown_all() -> None:
    """Terminate all registered workers (web shutdown hook, USER_SOP G12)."""
    for key in list(_WORKERS):
        stop_worker(*key)


def make_default_spawner(module: str = "collection") -> Callable[[int], None]:
    """Return a spawner(account_id) that launches the module worker on demand.

    Idempotent within the freshness window: a fresh heartbeat → no-op. Failures
    are logged, never raised (request returns; watchdog reaps zombie later).
    """

    def _spawn(account_id: int) -> None:
        if account_id is None:
            return
        if _heartbeat_fresh():
            logger.debug(f"[spawner] worker heartbeat fresh, skipping spawn for account {account_id}")
            return
        try:
            ensure_worker(module, account_id)
        except Exception as exc:
            # Don't break the request — the heartbeat watchdog will reap the
            # zombie `running` task → paused + WS within 30s.
            logger.error(f"[spawner] failed to spawn worker for account {account_id}: {exc}")

    return _spawn
