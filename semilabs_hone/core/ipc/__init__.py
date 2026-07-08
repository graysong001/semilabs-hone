"""core/ipc — file-based cross-process task bus.

Protocol schemas, path helpers, client (web side), and server (worker side).
Design: docs/skim_design.md §6
"""
from .protocol import IPCRequest, IPCProgress, IPCResult
from .paths import (
    requests_dir,
    results_dir,
    progress_dir,
    control_cancel_dir,
    request_path,
    result_path,
    progress_path,
    cancel_sentinel,
    atomic_write_json,
    read_json_if_exists,
)
from .client import IPCClient
from .server import serve_worker

__all__ = [
    "IPCRequest",
    "IPCProgress",
    "IPCResult",
    "requests_dir",
    "results_dir",
    "progress_dir",
    "control_cancel_dir",
    "request_path",
    "result_path",
    "progress_path",
    "cancel_sentinel",
    "atomic_write_json",
    "read_json_if_exists",
    "IPCClient",
    "serve_worker",
]
