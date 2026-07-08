"""IPC client — web-side interface to the file-based task bus.

Methods:
    submit(req) -> str          atomically write request, return request_id
    poll_progress(request_id)   read latest progress snapshot
    wait_result(request_id, timeout) -> IPCResult  poll result file every 1s
    cancel(request_id)          write cancel sentinel
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .protocol import IPCProgress, IPCRequest, IPCResult
from .paths import (
    atomic_write_json,
    cancel_sentinel,
    progress_path,
    read_json_if_exists,
    request_path,
    result_path,
)

if TYPE_CHECKING:
    pass


class IPCClient:
    """Web-side client for the file-based IPC bus."""

    def submit(self, req: IPCRequest) -> str:
        """Atomically write a request file and return its request_id."""
        atomic_write_json(request_path(req.request_id), req.model_dump())
        return req.request_id

    async def poll_progress(self, request_id: str) -> IPCProgress | None:
        """Read the latest progress snapshot, or None if not yet written."""
        data = read_json_if_exists(progress_path(request_id))
        if data is None:
            return None
        return IPCProgress(**data)

    async def wait_result(
        self, request_id: str, timeout: float = 300.0
    ) -> IPCResult:
        """Poll the result file every 1s until it appears or timeout.

        Raises asyncio.TimeoutError if no result within the timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = read_json_if_exists(result_path(request_id))
            if data is not None:
                return IPCResult(**data)
            await asyncio.sleep(1)
        raise asyncio.TimeoutError(
            f"wait_result timed out after {timeout}s for {request_id}"
        )

    def cancel(self, request_id: str) -> None:
        """Write a cancel sentinel file to signal the worker to stop."""
        sentinel = cancel_sentinel(request_id)
        # Touch the file atomically
        atomic_write_json(sentinel, {"cancelled": True})
