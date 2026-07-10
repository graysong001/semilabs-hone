"""IPC server — generic worker main loop for any module.

Core function:
    async serve_worker(module, handler_registry, on_progress) -> None

Algorithm:
    1. Poll requests/ for the earliest file whose .module matches ours.
    2. Read-after-burn: load request into memory, then immediately delete the
       file (PRD §7.2 redline — zombie instructions cause infinite re-execution).
    3. Bad-JSON tolerance: a corrupt/truncated request file is caught, logged,
       and burned without crashing the loop (PRD §8.3 场景 3.1).
    4. Dispatch the handler by .op, streaming progress updates.
    5. Consume control directives {pause,resume,stop} from control/ — read after
       burn — before/after the handler (PRD §3.4 / §8.3 场景 3.2).
    6. Self-check cancel sentinel before each step.
    7. Write heartbeat every ~10s while alive (PRD §3.3).
    8. Write result (ok/error/paused/cancelled/need_human).
"""
from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Callable

from loguru import logger

from .paths import (
    atomic_write_json,
    burn,
    cancel_sentinel,
    control_path,
    read_json_if_exists,
    request_path,
    result_path,
    requests_dir,
    progress_path,
    progress_dir,
    write_heartbeat,
)
from .protocol import IPCProgress, IPCRequest, IPCResult

HandlerFn = Callable[[dict, Callable[[str, dict | None], None]], dict]
OnProgressFn = Callable[[str, str, dict], None]

# Heartbeat cadence (PRD §3.3: every ~10s while worker alive).
HEARTBEAT_INTERVAL = 10.0


def _list_requests() -> list[Path]:
    """Return request files sorted by mtime (earliest first)."""
    d = requests_dir()
    if not d.exists():
        return []
    files = list(d.glob("*.json"))
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _is_cancelled(request_id: str) -> bool:
    """Check if a legacy cancel sentinel exists."""
    return cancel_sentinel(request_id).exists()


def _write_progress(request_id: str, message: str, data: dict | None = None) -> None:
    """Overwrite progress file atomically."""
    prog = IPCProgress(request_id=request_id, message=message, data=data)
    atomic_write_json(progress_path(request_id), prog.model_dump())


def _write_result(
    request_id: str,
    status: str,
    data: dict | None = None,
    error: dict | None = None,
    ws_events: list[dict] | None = None,
) -> None:
    """Write final result atomically."""
    res = IPCResult(
        request_id=request_id,
        status=status,
        data=data,
        error=error,
        ws_events=ws_events,
    )
    atomic_write_json(result_path(request_id), res.model_dump())


def _consume_control(request_id: str) -> str | None:
    """Read a control directive for a request, then burn it (read-after-burn).

    Returns one of {"pause", "resume", "stop"} or None when no directive is
    present. Corrupt JSON is caught, logged, and burned without raising
    (PRD §8.3 场景 3.1 tolerance extends to control/ files).
    """
    p = control_path(request_id)
    if not p.exists():
        return None
    try:
        data = read_json_if_exists(p)
    except Exception as exc:
        logger.warning(f"[ipc] bad control JSON for {request_id}, burning: {exc}")
        burn(p)
        return None
    if data is None:
        return None
    action = data.get("action")
    # Burn immediately after loading into memory (PRD §7.2 / §8.3 场景 3.2).
    burn(p)
    if action in ("pause", "resume", "stop"):
        return action
    logger.warning(f"[ipc] unknown control action '{action}' for {request_id}")
    return None


def _load_request_or_burn(path: Path) -> dict | None:
    """Load a request file into memory; burn it regardless of parse outcome.

    Read-after-burn (PRD §7.2): the file is deleted the instant it is loaded
    into memory. Bad JSON is caught, logged, and burned without crashing the
    worker (PRD §8.3 场景 3.1). Returns None if the file is missing, corrupt,
    or already had a result written.
    """
    rid_hint = path.stem
    try:
        data = read_json_if_exists(path)
    except Exception as exc:
        logger.warning(f"[ipc] bad request JSON {path.name}, burning: {exc}")
        burn(path)
        return None
    if data is None:
        return None
    # Burn the request file the instant it is in memory (PRD §7.2 redline).
    burn(path)
    rid = data.get("request_id", rid_hint)
    # Skip if a result was already written (idempotent re-poll guard).
    if result_path(rid).exists():
        logger.debug(f"[ipc] {rid} already has a result, skipping re-poll")
        return None
    return data


async def serve_worker(
    module: str,
    handler_registry: dict[str, HandlerFn],
    on_progress: OnProgressFn | None = None,
    poll_interval: float = 1.0,
) -> None:
    """Main loop for a module worker.

    Args:
        module: this worker's module name (e.g. "collection").
        handler_registry: {op: handler_fn} dispatch table.
            Each handler receives (payload, progress_cb) and returns a dict.
            progress_cb(message, data=None) writes progress.
        on_progress: optional callback(request_id, message, data) for
            external notification (e.g. logging).
        poll_interval: seconds between request directory polls.
    """
    last_heartbeat = 0.0

    while True:
        # Heartbeat: write at most every HEARTBEAT_INTERVAL (PRD §3.3).
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            write_heartbeat("alive")
            last_heartbeat = now

        req_files = _list_requests()
        picked = None
        req_data = None

        for f in req_files:
            data = _load_request_or_burn(f)
            if data is None:
                continue
            if data.get("module") == module:
                picked = f
                req_data = data
                break

        if picked is None:
            await asyncio.sleep(poll_interval)
            continue

        request_id = req_data["request_id"]
        op = req_data["op"]
        payload = req_data.get("payload", {})

        # Self-check legacy cancel sentinel + control directives before dispatch.
        if _is_cancelled(request_id):
            _write_result(request_id, "cancelled")
            continue
        pre_control = _consume_control(request_id)
        if pre_control == "stop":
            _write_result(request_id, "cancelled")
            continue
        if pre_control == "pause":
            _write_progress(request_id, "paused", {"reason": "control:pause"})
            _write_result(
                request_id,
                "paused",
                error={"category": "Paused", "message": "paused by control",
                       "fix_hint": "resume via a new request when ready"},
            )
            continue

        # Dispatch
        handler = handler_registry.get(op)
        if handler is None:
            _write_result(
                request_id,
                "error",
                error={
                    "category": "UnknownOp",
                    "message": f"No handler for op '{op}' in module '{module}'",
                    "fix_hint": f"Register a handler for '{op}' in {module}'s handler registry.",
                },
            )
            continue

        # Run handler with cancel checking and progress streaming
        try:

            def progress_cb(message: str, data: dict | None = None) -> None:
                _write_progress(request_id, message, data)
                if on_progress:
                    on_progress(request_id, message, data or {})

            # Support both sync and async handlers
            is_async = inspect.iscoroutinefunction(handler)
            if is_async:
                handler_result = await handler(payload, progress_cb)
            else:
                handler_result = handler(payload, progress_cb)

            # Check if handler returned a dict with its own status (e.g. "paused")
            if isinstance(handler_result, dict) and "status" in handler_result:
                _write_result(
                    request_id,
                    handler_result["status"],
                    data={k: v for k, v in handler_result.items() if k != "status"},
                )
                continue

            # Check cancel/control after handler returns.
            if _is_cancelled(request_id):
                _write_result(request_id, "cancelled")
                continue
            post_control = _consume_control(request_id)
            if post_control == "stop":
                _write_result(request_id, "cancelled")
                continue

            _write_result(request_id, "ok", data=handler_result)

        except Exception as exc:
            # Categorize the error
            category = type(exc).__name__
            fix_hint = str(exc)
            if hasattr(exc, "category"):
                category = exc.category
            if hasattr(exc, "fix_hint"):
                fix_hint = exc.fix_hint

            _write_result(
                request_id,
                "error",
                error={
                    "category": category,
                    "message": str(exc),
                    "fix_hint": fix_hint,
                },
            )
