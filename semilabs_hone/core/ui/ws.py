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
