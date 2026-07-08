"""IPC server — generic worker main loop for any module.

Core function:
    async serve_worker(module, handler_registry, on_progress) -> None

Algorithm:
    1. Poll requests/ for the earliest file whose .module matches ours.
    2. Look up the handler by .op in handler_registry.
    3. Run the handler, streaming progress updates.
    4. Self-check cancel sentinel before each step.
    5. Write result (ok/error/paused/cancelled).
    6. On exception, write error result with category/fix_hint.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

from .paths import (
    atomic_write_json,
    cancel_sentinel,
    read_json_if_exists,
    request_path,
    result_path,
    requests_dir,
    progress_path,
    progress_dir,
)
from .protocol import IPCProgress, IPCRequest, IPCResult

HandlerFn = Callable[[dict, Callable[[str, dict | None], None]], dict]
OnProgressFn = Callable[[str, str, dict], None]


def _list_requests() -> list[Path]:
    """Return request files sorted by mtime (earliest first)."""
    d = requests_dir()
    if not d.exists():
        return []
    files = list(d.glob("*.json"))
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _is_cancelled(request_id: str) -> bool:
    """Check if a cancel sentinel exists."""
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
    while True:
        req_files = _list_requests()
        picked = None
        req_data = None

        for f in req_files:
            data = read_json_if_exists(f)
            if data is None:
                continue
            rid = data.get("request_id", "")
            # Skip if already has a result
            if result_path(rid).exists():
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

            # Self-check cancel before starting
            if _is_cancelled(request_id):
                _write_result(request_id, "cancelled")
                continue

            handler_result = handler(payload, progress_cb)

            # Check cancel after handler returns
            if _is_cancelled(request_id):
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
