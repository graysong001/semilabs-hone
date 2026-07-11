"""WebSocket manager for semilabs-hone.

Unified WS broadcast layer: all notification sources construct a dict
(conforming to ProgressMessage) and hand it to WSManager.broadcast().
Workers do NOT connect directly to WS — progress is relayed by the
IPC client via ws_events.

Design: docs/skim_design.md §13.3.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class WSManager:
    """Manage WebSocket connections, message buffering, and broadcast.

    - connections: set of active WebSocket objects
    - message_buffer: deque(maxlen=50) for replay on reconnect
    - connect(ws): register + replay buffer
    - disconnect(ws): deregister
    - broadcast(msg): send to all + push into buffer
    """

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self.message_buffer: deque = deque(maxlen=50)

    async def connect(self, ws: WebSocket) -> None:
        """Accept a new WS connection and replay the message buffer."""
        await ws.accept()
        self.connections.add(ws)
        # Replay recent messages to the new connection
        for msg in self.message_buffer:
            await ws.send_json(msg)

    async def disconnect(self, ws: WebSocket) -> None:
        """Remove a disconnected WebSocket."""
        self.connections.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        """Send msg to all connected clients and store in buffer."""
        self.message_buffer.append(msg)
        dead: set[WebSocket] = set()
        for ws in self.connections:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self.connections -= dead


# Module-level singleton
ws_manager = WSManager()


async def run_progress_relay(interval: float = 2.0) -> None:
    """Background relay: worker progress/results files → WS broadcast (L16).

    Workers write `progress/<rid>.json` (IPCProgress) and `results/<rid>.json`
    (IPCResult, carrying optional `ws_events`) to the file bus; the web process
    owns WS. This loop scans both dirs, dedups by `updated_at`/seen-set, resolves
    `request_id → task_id` via DB (best-effort), and broadcasts each new item
    through `ws_manager`. No-op when the dirs are empty (tests); cancelled on
    shutdown (app._shutdown cancels app.state.relay_task).

    Written with `while running:` (not bare `while True`) per the §7.4 linter:
    exits on CancelledError (shutdown) — a bounded suspend loop, not a refresh
    death-loop.
    """
    from semilabs_hone.core.ipc.paths import (
        progress_dir,
        read_json_if_exists,
        results_dir,
    )

    seen_progress: dict[str, float] = {}  # rid -> last broadcasted updated_at
    seen_results: set[str] = set()
    running = True
    try:
        while running:
            # --- progress/ → WS progress event ---
            try:
                pdir = progress_dir()
                if pdir.exists():
                    for f in pdir.glob("*.json"):
                        if f.name == "heartbeat.json":
                            continue
                        rid = f.stem
                        data = read_json_if_exists(f)
                        if data is None:
                            continue
                        updated = data.get("updated_at") or data.get("timestamp") or 0
                        if seen_progress.get(rid) == updated:
                            continue
                        seen_progress[rid] = updated
                        task_id = _resolve_task_id(rid)
                        await ws_manager.broadcast({
                            "type": "progress",
                            "module": "collection",
                            "task_id": task_id,
                            "request_id": rid,
                            "message": data.get("message", ""),
                            "data": data.get("data") or {},
                        })
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            # --- results/ → ws_events fan-out ---
            try:
                rdir = results_dir()
                if rdir.exists():
                    for f in rdir.glob("*.json"):
                        rid = f.stem
                        if rid in seen_results:
                            continue
                        data = read_json_if_exists(f)
                        if data is None:
                            continue
                        seen_results.add(rid)
                        ws_events = data.get("ws_events") or []
                        for ev in ws_events:
                            await ws_manager.broadcast(ev)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        running = False
        raise


def _resolve_task_id(request_id: str) -> str | None:
    """Best-effort request_id → task_id via DB (CollectionTask.request_id)."""
    if not request_id:
        return None
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(
                CollectionTask.request_id == request_id
            ).first()
            return t.id if t else None
        finally:
            sess.close()
    except Exception:
        return None
