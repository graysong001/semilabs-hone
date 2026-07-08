"""IPC file paths and atomic I/O helpers.

IMPORTANT: all directory constants are **functions** that lazily read
config.IPC_ROOT, so test fixtures can monkeypatch config.IPC_ROOT at
runtime (the DM-02 lesson: never freeze paths at import time).

Directory layout under data/ipc/:
    requests/    — incoming request JSON files
    results/     — final result JSON files
    progress/    — streaming progress JSON files
    control/cancel/ — cancel sentinel files (touch)
"""
from __future__ import annotations

import json
import os
import tempfile
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


def control_cancel_dir() -> Path:
    return _ipc_root() / "control" / "cancel"


def request_path(request_id: str) -> Path:
    return requests_dir() / f"{request_id}.json"


def result_path(request_id: str) -> Path:
    return results_dir() / f"{request_id}.json"


def progress_path(request_id: str) -> Path:
    return progress_dir() / f"{request_id}.json"


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
    """Read and return parsed JSON, or None if the file does not exist."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
