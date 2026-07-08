"""IPC message schemas for the file-based task bus.

Three Pydantic models: IPCRequest (client -> worker),
IPCProgress (streaming updates), IPCResult (final outcome).
Design: docs/skim_design.md §6.2
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


class IPCRequest(BaseModel):
    """Task request written by the web client into requests/."""
    request_id: str
    module: str
    op: str
    account_id: int | None = None
    payload: dict = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class IPCProgress(BaseModel):
    """Streaming progress update written by the worker into progress/."""
    request_id: str
    message: str
    data: dict | None = None
    updated_at: float = Field(default_factory=time.time)


class IPCResult(BaseModel):
    """Final result written by the worker into results/.

    status: "ok" | "error" | "paused" | "cancelled"
    error: {category, message, fix_hint} when status=="error"
    ws_events: optional list of WS event dicts for client to broadcast.
    """
    request_id: str
    status: Literal["ok", "error", "paused", "cancelled"]
    data: dict | None = None
    error: dict | None = None
    ws_events: list[dict] | None = None
    completed_at: float = Field(default_factory=time.time)
