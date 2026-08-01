"""WebSocket manager for semilabs-hone.

Unified WS broadcast layer: all notification sources construct a dict
(conforming to ProgressMessage) and hand it to WSManager.broadcast().
Workers do NOT connect directly to WS — progress is relayed by the
IPC client via ws_events.

Design: docs/skim_design.md §13.3.
"""
from __future__ import annotations

import asyncio
import time
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
        """Accept a new WS connection and replay the message buffer.

        Replayed messages are flagged ``replay: true`` so the frontend can
        restore progress state from them but must NOT fire side effects
        (toasts / page reloads) — otherwise a buffered login_success would
        trigger accounts.html reload → WS reconnect → replay → reload loop.
        """
        await ws.accept()
        self.connections.add(ws)
        # Replay recent messages to the new connection (flagged, see docstring)
        for msg in self.message_buffer:
            await ws.send_json({**msg, "replay": True})

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


#: Worker progress messages that carry a first-class meaning for the UI.
#: Everything else is relayed as a plain "progress" message (USER_SOP G4:
#: the QR screenshot used to arrive as anonymous progress and was lost).
PROGRESS_EVENT_TYPES = {
    "qr_ready": "qr_ready",
    "captcha_required": "captcha_required",
    "login_success": "login_success",
    # 账号验证结果（handler_validate）：app.js/accounts.html 都监听这个类型，
    # 此前 validate_done 无映射导致前端永远收不到验证反馈。
    "session_status": "session_status",
    "disk_warn": "disk_warn",
    "scrape_failed": "task_failed",
    # 平台探测器事件 (Task #7)
    "discover_xhr": "discover_xhr",
    "discover_dom": "discover_dom",
    "discover_ready": "discover_ready",
    "discover_status": "discover_status",
}

#: Bus files untouched for this long before relay startup are stale residue
#: (yesterday's qr_ready / failed login results), not live progress — never
#: broadcast them into the WS buffer, or a web restart replays old events as
#: fresh toasts. Live workers rewrite their progress file every step, so a
#: file older than this window is provably dead (wait_result times out at 120s).
_STALE_FILE_AGE_S = 300.0


async def run_progress_relay(interval: float = 2.0) -> None:
    """Background relay: worker progress/results files → WS broadcast (L16).

    Workers write `progress/<rid>.json` (IPCProgress) and `results/<rid>.json`
    (IPCResult, carrying optional `ws_events`) to the file bus; the web process
    owns WS. This loop scans both dirs, dedups by `updated_at`/seen-set, resolves
    `request_id → task_id` via DB (best-effort), and broadcasts each new item
    through `ws_manager`. Key worker progress messages are promoted to
    first-class WS event types (G4). A result is broadcast (ws_events + one
    terminal event) and then its file is deleted — consume-on-read (F11), the
    bus must not accumulate. No-op when the dirs are empty (tests); cancelled
    on shutdown (app._shutdown cancels app.state.relay_task).

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
    started_at = time.time()
    running = True
    try:
        while running:
            # --- progress/ → WS progress event (typed when first-class, G4) ---
            try:
                pdir = progress_dir()
                if pdir.exists():
                    for f in pdir.glob("*.json"):
                        if f.name == "heartbeat.json":
                            continue
                        try:
                            if f.stat().st_mtime < started_at - _STALE_FILE_AGE_S:
                                continue  # stale bus residue, see _STALE_FILE_AGE_S
                        except OSError:
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
                        message = data.get("message", "")
                        payload = data.get("data") or {}
                        await ws_manager.broadcast({
                            "type": PROGRESS_EVENT_TYPES.get(message, "progress"),
                            "module": "collection",
                            "task_id": task_id,
                            "request_id": rid,
                            # first-class events (session_status) carry their own
                            # user-facing message; app.js reads valid at top level.
                            "message": payload.get("message") or message,
                            "data": payload,
                            **({"valid": payload["valid"]} if "valid" in payload else {}),
                        })
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            # --- results/ → ws_events fan-out + terminal event, then consume ---
            try:
                rdir = results_dir()
                if rdir.exists():
                    for f in rdir.glob("*.json"):
                        try:
                            if f.stat().st_mtime < started_at - _STALE_FILE_AGE_S:
                                # stale result: its waiter timed out long ago;
                                # delete without broadcasting (no error bombing
                                # after a web restart)
                                try:
                                    f.unlink()
                                except OSError:
                                    pass
                                continue
                        except OSError:
                            continue
                        rid = f.stem
                        data = read_json_if_exists(f)
                        if data is None:
                            continue
                        for ev in data.get("ws_events") or []:
                            await ws_manager.broadcast(ev)
                        await _broadcast_terminal(rid, data)
                        try:
                            f.unlink()  # consume-on-read (F11)
                        except OSError:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        running = False
        raise


async def _broadcast_terminal(request_id: str, data: dict) -> None:
    """Broadcast one terminal WS event for a finished IPC result."""
    status = data.get("status")
    task_id = _resolve_task_id(request_id)
    base = {"request_id": request_id, "task_id": task_id}
    err = data.get("error") or {}
    if status == "ok":
        if task_id is not None:
            await ws_manager.broadcast({**base, "type": "task_completed"})
        else:
            await ws_manager.broadcast({
                **base, "type": "progress", "message": "done",
                "data": data.get("data") or {},
            })
    elif status == "cancelled":
        await ws_manager.broadcast({**base, "type": "warn", "message": "任务已取消"})
    elif status == "need_human":
        await ws_manager.broadcast({
            **base, "type": "need_human",
            "message": err.get("message", "需要人工处理"),
        })
    elif status == "paused":
        msg_type = "captcha_required" if err.get("category") == "captcha" else "warn"
        await ws_manager.broadcast({
            **base, "type": msg_type, "message": err.get("message", "已暂停"),
        })
    elif status == "error":
        await ws_manager.broadcast({
            **base,
            "type": "error",
            "category": err.get("category"),
            "message": err.get("message", "unknown error"),
        })


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
