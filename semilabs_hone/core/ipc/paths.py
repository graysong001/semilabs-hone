"""IPC file paths and atomic I/O helpers.

IMPORTANT: all directory constants are **functions** that lazily read
config.IPC_ROOT, so test fixtures can monkeypatch config.IPC_ROOT at
runtime (the DM-02 lesson: never freeze paths at import time).

Directory layout under data/ipc/ (PRD §3.4 / §7.2):
    requests/    — incoming request JSON files (Worker reads, then burns)
    results/     — final result / ack JSON files
    progress/    — streaming progress JSON files + heartbeat.json
    control/     — intervention cmds {pause,resume,stop} (Worker reads, then burns)
    control/cancel/ — legacy cancel sentinel files (kept for backward compat)

Read-after-burn redline (PRD §7.2): request & control files MUST be deleted
the instant the Worker has loaded them into memory, else zombie instructions
cause infinite re-execution.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _ipc_root() -> Path:
    """Lazily read IPC_ROOT from config so tests can monkeypatch it."""
    from config import IPC_ROOT
    return IPC_ROOT


def requests_dir() -> Path:
    return _ipc_root() / "requests"


def results_dir() -> Path:
    return _ipc_root() / "results"


def progress_dir() -> Path:
    return _ipc_root() / "progress"


def control_dir() -> Path:
    """Flat control/ dir for {pause,resume,stop} cmd files (PRD §7.2)."""
    return _ipc_root() / "control"


def control_cancel_dir() -> Path:
    """Legacy cancel sentinel dir (backward compat)."""
    return _ipc_root() / "control" / "cancel"


def request_path(request_id: str) -> Path:
    return requests_dir() / f"{request_id}.json"


def result_path(request_id: str) -> Path:
    return results_dir() / f"{request_id}.json"


def progress_path(request_id: str) -> Path:
    return progress_dir() / f"{request_id}.json"


def control_path(request_id: str) -> Path:
    """Control cmd file: control/ctrl_<request_id>.json (PRD §7.2)."""
    return control_dir() / f"ctrl_{request_id}.json"


def heartbeat_path() -> Path:
    """Worker heartbeat: progress/heartbeat.json (PRD §3.3)."""
    return progress_dir() / "heartbeat.json"


def cancel_sentinel(request_id: str) -> Path:
    return control_cancel_dir() / f"{request_id}"


def atomic_write_json(path: Path, obj: dict | list) -> None:
    """Write JSON atomically via a .tmp sibling + os.rename.

    Creates parent directories if needed.  Never leaves a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def read_json_if_exists(path: Path) -> dict | None:
    """Read and return parsed JSON, or None if the file does not exist.

    Raises json.JSONDecodeError for corrupt/truncated files — callers in the
    server loop MUST catch this and burn the bad file (PRD §8.3 场景 3.1).
    """
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def burn(path: Path) -> None:
    """Read-after-burn: delete a file, swallowing NotFound (PRD §7.2)."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def write_heartbeat(message: str | None = None, *, now: float | None = None) -> None:
    """Overwrite the heartbeat file with a fresh timestamp (PRD §3.3).

    Worker calls this every ~10s while alive. Web polls it; stale >30s =>
    worker presumed dead => task auto-paused.
    """
    ts = now if now is not None else time.time()
    atomic_write_json(
        heartbeat_path(),
        {"timestamp": ts, "message": message or "alive", "updated_at": ts},
    )


def heartbeat_age(now: float | None = None) -> float | None:
    """Seconds since the heartbeat was last written, or None if absent."""
    data = read_json_if_exists(heartbeat_path())
    if not data:
        return None
    ts = data.get("timestamp") or data.get("updated_at")
    if ts is None:
        return None
    cur = now if now is not None else time.time()
    try:
        return max(0.0, cur - float(ts))
    except (TypeError, ValueError):
        return None

