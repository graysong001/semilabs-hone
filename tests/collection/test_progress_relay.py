"""S9a L16 — WS progress relay: worker progress/results files → WS broadcast.

The relay scans progress/ and results/, dedups, resolves request_id→task_id,
and broadcasts via ws_manager. Workers do not connect to WS directly (contract
§7/§8); this loop is the bridge.
"""
from __future__ import annotations

import asyncio

import pytest

from semilabs_hone.core.ui import ws as ws_mod


async def _run_relay_briefly(monkeypatch, broadcasts, *, sleep=0.3):
    """Start run_progress_relay, let it tick once, then cancel. Records broadcasts."""
    async def _fake_broadcast(msg):
        broadcasts.append(msg)

    monkeypatch.setattr(ws_mod.ws_manager, "broadcast", _fake_broadcast)
    task = asyncio.create_task(ws_mod.run_progress_relay(interval=0.05))
    try:
        await asyncio.sleep(sleep)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _seed_task_with_rid(db_session, rid):
    from semilabs_hone.core.models.task import CollectionTask
    t = CollectionTask(account_id=1, platform="xiaohongshu", status="running",
                       max_posts_per_keyword=3, request_id=rid)
    db_session.add(t)
    db_session.commit()
    return t.id


class TestProgressRelay:
    async def test_progress_file_broadcast_as_progress_event(self, tmp_data_dir, db_session, monkeypatch):
        from semilabs_hone.core.ipc.paths import progress_path
        from semilabs_hone.core.ipc.protocol import IPCProgress
        import time

        rid = "relay-prog"
        tid = _seed_task_with_rid(db_session, rid)
        prog = IPCProgress(request_id=rid, message="phase2_search", data={"keyword": "k"})
        # Atomic write via the protocol dump (paths uses atomic_write_json).
        from semilabs_hone.core.ipc.paths import atomic_write_json
        atomic_write_json(progress_path(rid), prog.model_dump())

        broadcasts: list = []
        await _run_relay_briefly(monkeypatch, broadcasts)

        prog_msgs = [b for b in broadcasts if b.get("type") == "progress"]
        assert prog_msgs, "progress file must be relayed as a WS progress event"
        msg = prog_msgs[0]
        assert msg["request_id"] == rid
        assert msg["task_id"] == tid  # request_id → task_id resolved via DB
        assert msg["message"] == "phase2_search"

    async def test_results_ws_events_fanned_out(self, tmp_data_dir, db_session, monkeypatch):
        from semilabs_hone.core.ipc.paths import atomic_write_json, result_path

        rid = "relay-res"
        _seed_task_with_rid(db_session, rid)
        ws_events = [
            {"type": "task_completed", "task_id": "x", "message": "done"},
            {"type": "warn", "message": "disk 80%"},
        ]
        atomic_write_json(result_path(rid), {
            "request_id": rid, "status": "ok", "ws_events": ws_events,
        })

        broadcasts: list = []
        await _run_relay_briefly(monkeypatch, broadcasts)

        # Both ws_events were broadcast.
        assert any(b.get("type") == "task_completed" for b in broadcasts)
        assert any(b.get("type") == "warn" for b in broadcasts)

    async def test_progress_not_rebroadcast_for_same_updated_at(self, tmp_data_dir, db_session, monkeypatch):
        """Same updated_at → broadcast once, not every tick."""
        from semilabs_hone.core.ipc.paths import atomic_write_json, progress_path
        from semilabs_hone.core.ipc.protocol import IPCProgress

        rid = "relay-dedup"
        _seed_task_with_rid(db_session, rid)
        prog = IPCProgress(request_id=rid, message="resting")
        atomic_write_json(progress_path(rid), prog.model_dump())

        broadcasts: list = []
        await _run_relay_briefly(monkeypatch, broadcasts, sleep=0.25)

        prog_msgs = [b for b in broadcasts if b.get("request_id") == rid]
        assert len(prog_msgs) == 1  # deduped — not rebroadcast each tick

    async def test_empty_dirs_no_broadcasts(self, tmp_data_dir, monkeypatch):
        """No progress/results files → relay broadcasts nothing."""
        broadcasts: list = []
        await _run_relay_briefly(monkeypatch, broadcasts)
        assert broadcasts == []
