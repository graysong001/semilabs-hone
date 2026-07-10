"""IPC bus module tests — submit/result, cancel, atomic write, progress, error.

Uses the tmp_data_dir fixture from conftest.py to isolate all file I/O.
Naming: test_<method>_<scenario>_<expected>
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from semilabs_hone.core.ipc.protocol import IPCRequest, IPCProgress, IPCResult
from semilabs_hone.core.ipc.paths import (
    requests_dir,
    results_dir,
    progress_dir,
    control_cancel_dir,
    control_dir,
    control_path,
    heartbeat_path,
    request_path,
    result_path,
    progress_path,
    cancel_sentinel,
    atomic_write_json,
    read_json_if_exists,
    burn,
    heartbeat_age,
    write_heartbeat,
)
from semilabs_hone.core.ipc.client import IPCClient
from semilabs_hone.core.ipc.server import serve_worker


# ── Protocol tests ──────────────────────────────────────────────────

def test_protocol_ipc_request_model_fields():
    """IPCRequest has all required fields and sensible defaults."""
    req = IPCRequest(request_id="r1", module="collection", op="login")
    assert req.request_id == "r1"
    assert req.module == "collection"
    assert req.op == "login"
    assert req.account_id is None
    assert req.payload == {}
    assert isinstance(req.created_at, float)


def test_protocol_ipc_progress_model_fields():
    """IPCProgress serializes to dict and back."""
    prog = IPCProgress(request_id="r1", message="working", data={"step": 1})
    d = prog.model_dump()
    assert d["request_id"] == "r1"
    assert d["data"] == {"step": 1}


def test_protocol_ipc_result_ok():
    """IPCResult with status=ok."""
    res = IPCResult(request_id="r1", status="ok", data={"count": 42})
    assert res.status == "ok"
    assert res.data == {"count": 42}
    assert res.error is None
    assert res.ws_events is None


def test_protocol_ipc_result_error_with_category():
    """IPCResult error includes category and fix_hint."""
    res = IPCResult(
        request_id="r1",
        status="error",
        error={"category": "PageLoadError", "message": "timeout", "fix_hint": "retry later"},
    )
    assert res.error["category"] == "PageLoadError"


def test_protocol_ipc_result_status_values():
    """IPCResult accepts all five status literals incl. need_human (契约变更)."""
    for s in ["ok", "error", "paused", "cancelled", "need_human"]:
        r = IPCResult(request_id="x", status=s)
        assert r.status == s


def test_protocol_ipc_result_ws_events_field():
    """IPCResult has ws_events field for client to broadcast."""
    res = IPCResult(
        request_id="r1", status="ok", ws_events=[{"type": "progress", "data": {}}]
    )
    assert len(res.ws_events) == 1


# ── Paths tests ─────────────────────────────────────────────────────

def test_paths_directories_exist(tmp_data_dir):
    """requests/results/progress/control directories are accessible."""
    assert requests_dir().exists()
    assert results_dir().exists()
    assert progress_dir().exists()
    assert control_cancel_dir().exists()


def test_paths_request_path_builds_correctly(tmp_data_dir):
    """request_path returns the expected path."""
    p = request_path("abc123")
    assert p == requests_dir() / "abc123.json"
    assert str(p).endswith("abc123.json")


def test_paths_result_path_builds_correctly(tmp_data_dir):
    """result_path returns the expected path."""
    p = result_path("abc123")
    assert p == results_dir() / "abc123.json"


def test_paths_progress_path_builds_correctly(tmp_data_dir):
    """progress_path returns the expected path."""
    p = progress_path("abc123")
    assert p == progress_dir() / "abc123.json"


def test_paths_cancel_sentinel_builds_correctly(tmp_data_dir):
    """cancel_sentinel returns the expected path."""
    p = cancel_sentinel("abc123")
    assert p == control_cancel_dir() / "abc123"


def test_paths_lazy_reads_config(tmp_data_dir, monkeypatch):
    """Path functions lazily read config, not frozen at import time."""
    # tmp_data_dir already monkeypatched config.IPC_ROOT
    # So requests_dir() should point under tmp_data_dir
    assert "data" in str(requests_dir())


# ── atomic_write_json tests ─────────────────────────────────────────

def test_atomic_write_json_roundtrip(tmp_data_dir):
    """Write then read returns the same data."""
    path = requests_dir() / "test.json"
    obj = {"request_id": "r1", "module": "test", "op": "echo"}
    atomic_write_json(path, obj)
    assert read_json_if_exists(path) == obj


def test_atomic_write_json_no_partial_file(tmp_data_dir):
    """atomic_write_json does not leave a .tmp file behind."""
    path = results_dir() / "test.json"
    atomic_write_json(path, {"x": 1})
    tmp = path.with_suffix(".tmp")
    assert not tmp.exists(), ".tmp file should not remain after atomic write"


def test_atomic_write_json_overwrites(tmp_data_dir):
    """Second write replaces the first atomically."""
    path = progress_dir() / "test.json"
    atomic_write_json(path, {"v": 1})
    atomic_write_json(path, {"v": 2})
    assert read_json_if_exists(path) == {"v": 2}


def test_atomic_write_json_creates_parent_dirs(tmp_data_dir):
    """atomic_write_json creates parent directories if missing."""
    deep = tmp_data_dir / "ipc" / "deep" / "nested" / "test.json"
    atomic_write_json(deep, {"k": "v"})
    assert read_json_if_exists(deep) == {"k": "v"}


def test_read_json_if_exists_returns_none(tmp_data_dir):
    """read_json_if_exists returns None for missing file."""
    assert read_json_if_exists(requests_dir() / "nonexistent.json") is None


# ── IPCClient tests ─────────────────────────────────────────────────

def test_client_submit_creates_request_file(tmp_data_dir):
    """IPCClient.submit writes a request file and returns the id."""
    client = IPCClient()
    req = IPCRequest(request_id="sub1", module="collection", op="login")
    rid = client.submit(req)
    assert rid == "sub1"
    assert request_path("sub1").exists()
    data = read_json_if_exists(request_path("sub1"))
    assert data["module"] == "collection"


def test_client_submit_returns_request_id(tmp_data_dir):
    """submit returns the request_id from the request."""
    client = IPCClient()
    req = IPCRequest(request_id="rid42", module="test", op="echo", payload={"x": 1})
    rid = client.submit(req)
    assert rid == "rid42"


@pytest.mark.asyncio
async def test_client_poll_progress_returns_none_when_no_progress(tmp_data_dir):
    """poll_progress returns None if no progress file exists yet."""
    client = IPCClient()
    prog = await client.poll_progress("no_exist")
    assert prog is None


@pytest.mark.asyncio
async def test_client_poll_progress_returns_data(tmp_data_dir):
    """poll_progress reads and returns IPCProgress."""
    atomic_write_json(
        progress_path("pr1"),
        IPCProgress(request_id="pr1", message="step1", data={"n": 1}).model_dump(),
    )
    client = IPCClient()
    prog = await client.poll_progress("pr1")
    assert prog is not None
    assert prog.message == "step1"
    assert prog.data == {"n": 1}


@pytest.mark.asyncio
async def test_client_wait_result_returns_ok(tmp_data_dir):
    """wait_result polls and returns IPCResult when it appears."""
    rid = "wr1"

    # Simulate result appearing after a short delay
    async def write_result_later():
        await asyncio.sleep(0.3)
        atomic_write_json(
            result_path(rid),
            IPCResult(request_id=rid, status="ok", data={"count": 5}).model_dump(),
        )

    asyncio.create_task(write_result_later())
    client = IPCClient()
    res = await client.wait_result(rid, timeout=5)
    assert res.status == "ok"
    assert res.data == {"count": 5}


@pytest.mark.asyncio
async def test_client_wait_result_timeout(tmp_data_dir):
    """wait_result raises TimeoutError when no result appears."""
    client = IPCClient()
    with pytest.raises(asyncio.TimeoutError):
        await client.wait_result("nonexistent", timeout=0.5)


def test_client_cancel_creates_sentinel(tmp_data_dir):
    """cancel writes a sentinel file."""
    client = IPCClient()
    client.cancel("c1")
    assert cancel_sentinel("c1").exists()
    data = read_json_if_exists(cancel_sentinel("c1"))
    assert data["cancelled"] is True


# ── End-to-end: submit -> handler -> result (echo) ──────────────────

def _echo_handler(payload: dict, progress_cb):
    """Simple echo handler for testing."""
    progress_cb("echoing", {"input": payload})
    return {"echo": payload}


@pytest.mark.asyncio
async def test_e2e_submit_to_result_echo(tmp_data_dir):
    """Full cycle: client submits, server processes echo handler, client gets result."""
    client = IPCClient()
    req = IPCRequest(
        request_id="e2e1", module="test_module", op="echo", payload={"msg": "hello"}
    )
    rid = client.submit(req)
    assert rid == "e2e1"

    # Run server briefly to pick up the request
    handler_reg = {"echo": _echo_handler}
    task = asyncio.create_task(
        serve_worker("test_module", handler_reg, poll_interval=0.1)
    )

    # Give it time to process
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("e2e1", timeout=2)
    assert res.status == "ok"
    assert res.data["echo"] == {"msg": "hello"}


@pytest.mark.asyncio
async def test_e2e_progress_streaming(tmp_data_dir):
    """Handler streams progress updates visible to client."""
    def multi_step_handler(payload: dict, progress_cb):
        progress_cb("step 1", {"progress": 25})
        progress_cb("step 2", {"progress": 50})
        progress_cb("step 3", {"progress": 75})
        progress_cb("done", {"progress": 100})
        return {"steps": 4}

    client = IPCClient()
    req = IPCRequest(
        request_id="e2e_prog", module="test_mod", op="multi", payload={}
    )
    client.submit(req)

    handler_reg = {"multi": multi_step_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Check progress was written (last overwrite)
    prog = await client.poll_progress("e2e_prog")
    assert prog is not None
    assert "done" in prog.message


# ── Cancel sentinel test ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_cancel_stops_processing(tmp_data_dir):
    """When cancel sentinel exists, server writes cancelled result."""
    client = IPCClient()
    req = IPCRequest(
        request_id="cancel1", module="test_mod", op="slow", payload={}
    )
    client.submit(req)

    # Write cancel sentinel before server picks it up
    client.cancel("cancel1")

    handler_reg = {"slow": lambda p, cb: (cb("working"), time.sleep(10), {"done": True})[-1]}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("cancel1", timeout=2)
    assert res.status == "cancelled"


# ── Error result with category/fix_hint ─────────────────────────────

@pytest.mark.asyncio
async def test_server_error_result_has_category_and_fix_hint(tmp_data_dir):
    """Handler exception produces error result with category and fix_hint."""
    def failing_handler(payload, progress_cb):
        raise ValueError("something went wrong")

    client = IPCClient()
    req = IPCRequest(
        request_id="err1", module="test_mod", op="fail", payload={}
    )
    client.submit(req)

    handler_reg = {"fail": failing_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("err1", timeout=2)
    assert res.status == "error"
    assert res.error is not None
    assert "category" in res.error
    assert "fix_hint" in res.error


@pytest.mark.asyncio
async def test_server_unknown_op_error(tmp_data_dir):
    """Unknown op produces error result with UnknownOp category."""
    client = IPCClient()
    req = IPCRequest(
        request_id="unk1", module="test_mod", op="nonexistent_op", payload={}
    )
    client.submit(req)

    handler_reg = {}  # No handlers registered
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("unk1", timeout=2)
    assert res.status == "error"
    assert res.error["category"] == "UnknownOp"


# ── Lazy config path test ───────────────────────────────────────────

def test_paths_lazy_config_not_frozen_at_import(tmp_data_dir, monkeypatch):
    """Path helpers re-read config each call, not frozen at import time."""
    # After tmp_data_dir monkeypatch, paths should point under tmp
    rd = requests_dir()
    assert rd.exists()
    # Verify it's under the temp dir, not the real data/
    assert "tmp" in str(rd) or "pytest" in str(rd)


# ── PRD §7.2 control/ flat dir + control_path ─────────────────────────


def test_paths_control_dir_flat(tmp_data_dir):
    """control/ is flat (PRD §7.2), not nested under cancel/."""
    assert control_dir() == _ipc_root_pub() / "control"
    assert control_cancel_dir() == _ipc_root_pub() / "control" / "cancel"


def test_paths_control_path_uses_ctrl_prefix(tmp_data_dir):
    """control_path uses ctrl_<id>.json naming (PRD §7.2)."""
    p = control_path("t42")
    assert p.name == "ctrl_t42.json"
    assert p.parent == control_dir()


def _ipc_root_pub():
    from config import IPC_ROOT
    return IPC_ROOT


# ── PRD §7.2 read-after-burn (burn) ────────────────────────────────────


def test_burn_deletes_existing_file(tmp_data_dir):
    """burn() removes a file that exists."""
    p = request_path("burn1")
    atomic_write_json(p, {"x": 1})
    assert p.exists()
    burn(p)
    assert not p.exists()


def test_burn_swallows_missing_file(tmp_data_dir):
    """burn() on a non-existent file must NOT raise (PRD: no crash on re-burn)."""
    burn(request_path("never_existed"))
    # no exception raised == pass


# ── PRD §3.3 heartbeat primitives ──────────────────────────────────────


def test_write_then_read_heartbeat_age(tmp_data_dir):
    """write_heartbeat then heartbeat_age reflects elapsed seconds."""
    write_heartbeat("alive", now=1000.0)
    age = heartbeat_age(now=1005.0)
    assert age == 5.0


def test_heartbeat_age_none_when_absent(tmp_data_dir):
    """No heartbeat file => None (worker never started / dead)."""
    assert heartbeat_age(now=1000.0) is None


def test_heartbeat_stale_threshold(tmp_data_dir):
    """Heartbeat older than 30s is detectable as stale (PRD §3.3 watchdog)."""
    write_heartbeat(now=1000.0)
    assert heartbeat_age(now=1035.0) >= 30  # stale
    write_heartbeat(now=1025.0)
    assert heartbeat_age(now=1030.0) < 30  # fresh


# ── PRD §8.3 场景 3.1 bad-JSON tolerance ──────────────────────────────


def test_read_json_if_exists_raises_on_corrupt(tmp_data_dir):
    """Corrupt JSON must raise JSONDecodeError so the server can burn it."""
    p = request_path("corrupt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"action": "pau', encoding="utf-8")  # truncated
    with pytest.raises(json.JSONDecodeError):
        read_json_if_exists(p)


# ── PRD §7.2 server read-after-burn + §8.3 bad-JSON tolerance ───────────


@pytest.mark.asyncio
async def test_server_burns_request_after_pickup(tmp_data_dir):
    """Request file is deleted the instant the worker loads it (read-after-burn)."""
    from semilabs_hone.core.ipc import server

    client = IPCClient()
    req = IPCRequest(
        request_id="burn_req", module="test_mod", op="echo", payload={}
    )
    client.submit(req)
    assert request_path("burn_req").exists()

    handler_reg = {"echo": _echo_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Read-after-burn: the request file MUST be gone after pickup.
    assert not request_path("burn_req").exists()
    # And a result was still written.
    res = await client.wait_result("burn_req", timeout=2)
    assert res.status == "ok"


@pytest.mark.asyncio
async def test_server_bad_request_json_burned_no_crash(tmp_data_dir):
    """Corrupt request file is burned + logged; worker keeps running (PRD §8.3 场景3.1)."""
    # Drop a corrupt request file directly (not via IPCClient).
    bad = request_path("badjson")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"request_id": "badjson", "module": "test_mod", "op"', encoding="utf-8")

    # And a good one right after, to prove the loop survived.
    client = IPCClient()
    req = IPCRequest(request_id="good1", module="test_mod", op="echo", payload={})
    client.submit(req)

    handler_reg = {"echo": _echo_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Bad file burned, good request processed.
    assert not bad.exists()
    res = await client.wait_result("good1", timeout=2)
    assert res.status == "ok"


# ── PRD §3.4 / §8.3 场景3.2 control read-after-burn + dispatch ─────────


def test_consume_control_returns_action_and_burns(tmp_data_dir):
    """_consume_control reads action then burns the control file (read-after-burn)."""
    from semilabs_hone.core.ipc import server

    atomic_write_json(control_path("c1"), {"action": "pause"})
    assert control_path("c1").exists()

    action = server._consume_control("c1")
    assert action == "pause"
    # Read-after-burn: control file deleted the instant it is consumed.
    assert not control_path("c1").exists()


def test_consume_control_bad_json_burned_no_raise(tmp_data_dir):
    """Corrupt control JSON is burned + None returned, never raises (PRD §8.3)."""
    from semilabs_hone.core.ipc import server

    p = control_path("c2")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"action": "sto', encoding="utf-8")  # truncated

    assert server._consume_control("c2") is None
    assert not p.exists()


def test_consume_control_unknown_action_returns_none(tmp_data_dir):
    """Unknown action value is logged + burned, returns None."""
    from semilabs_hone.core.ipc import server

    atomic_write_json(control_path("c3"), {"action": "frobnicate"})
    assert server._consume_control("c3") is None
    assert not control_path("c3").exists()


@pytest.mark.asyncio
async def test_server_control_stop_writes_cancelled(tmp_data_dir):
    """control action=stop → cancelled result + control file burned."""
    client = IPCClient()
    req = IPCRequest(
        request_id="stop1", module="test_mod", op="echo", payload={}
    )
    client.submit(req)
    # Pre-write a stop directive (server consumes it before dispatch).
    atomic_write_json(control_path("stop1"), {"action": "stop"})

    handler_reg = {"echo": _echo_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("stop1", timeout=2)
    assert res.status == "cancelled"
    # Control file burned (read-after-burn, §8.3 场景3.2).
    assert not control_path("stop1").exists()


@pytest.mark.asyncio
async def test_server_control_pause_writes_paused(tmp_data_dir):
    """control action=pause → paused result + request + control burned."""
    client = IPCClient()
    req = IPCRequest(
        request_id="pause1", module="test_mod", op="echo", payload={}
    )
    client.submit(req)
    atomic_write_json(control_path("pause1"), {"action": "pause"})

    handler_reg = {"echo": _echo_handler}
    task = asyncio.create_task(
        serve_worker("test_mod", handler_reg, poll_interval=0.1)
    )
    await asyncio.sleep(0.4)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    res = await client.wait_result("pause1", timeout=2)
    assert res.status == "paused"
    assert not control_path("pause1").exists()
    assert not request_path("pause1").exists()


# ── PRD §3.3 server heartbeat writer ────────────────────────────────────


@pytest.mark.asyncio
async def test_server_writes_heartbeat(tmp_data_dir, monkeypatch):
    """serve_worker writes progress/heartbeat.json while alive (PRD §3.3)."""
    from semilabs_hone.core.ipc import server

    # Shrink the cadence so a heartbeat lands within the test window.
    monkeypatch.setattr(server, "HEARTBEAT_INTERVAL", 0.05)

    task = asyncio.create_task(
        serve_worker("test_mod", {}, poll_interval=0.05)
    )
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert heartbeat_path().exists()
    data = read_json_if_exists(heartbeat_path())
    assert data is not None
    assert "timestamp" in data


# ── PRD §3.3 web-side heartbeat watchdog ─────────────────────────────────


def test_check_heartbeat_stale_when_absent(tmp_data_dir):
    """No heartbeat → stale, age None (worker never started / dead)."""
    from semilabs_hone.core.ipc.watchdog import check_heartbeat

    is_stale, age, _msg = check_heartbeat(now=1000.0)
    assert is_stale is True
    assert age is None


def test_check_heartbeat_stale_when_old(tmp_data_dir):
    """Heartbeat older than threshold → stale."""
    from semilabs_hone.core.ipc.watchdog import check_heartbeat

    write_heartbeat(now=1000.0)
    is_stale, age, _msg = check_heartbeat(now=1035.0, threshold=30.0)
    assert is_stale is True
    assert age is not None and age >= 30


def test_check_heartbeat_fresh(tmp_data_dir):
    """Recent heartbeat → not stale."""
    from semilabs_hone.core.ipc.watchdog import check_heartbeat

    write_heartbeat(now=1000.0)
    is_stale, age, _msg = check_heartbeat(now=1005.0, threshold=30.0)
    assert is_stale is False
    assert age is not None and age < 30


def test_reap_stale_running_task_flips_to_paused(db_session, tmp_data_dir):
    """A stale heartbeat flips a running task → paused + returns a WS event."""
    from semilabs_hone.core.models.task import ScrapeTask
    from semilabs_hone.core.ipc.watchdog import reap_stale_running_task

    task = ScrapeTask(account_id=1, platform="xiaohongshu", status="running")
    db_session.add(task)
    db_session.commit()

    # Stale heartbeat (35s old > 30s threshold).
    write_heartbeat(now=1000.0)
    event = reap_stale_running_task(db_session, now=1035.0, threshold=30.0)

    assert event is not None
    assert event["type"] == "error"
    assert event["task_id"] == task.id
    db_session.refresh(task)
    assert task.status == "paused"
    assert task.error_category == "HeartbeatStale"


def test_reap_fresh_heartbeat_no_op(db_session, tmp_data_dir):
    """Fresh heartbeat → no task reaped, returns None."""
    from semilabs_hone.core.models.task import ScrapeTask
    from semilabs_hone.core.ipc.watchdog import reap_stale_running_task

    task = ScrapeTask(account_id=1, platform="xiaohongshu", status="running")
    db_session.add(task)
    db_session.commit()

    write_heartbeat(now=1000.0)
    event = reap_stale_running_task(db_session, now=1005.0, threshold=30.0)
    assert event is None
    db_session.refresh(task)
    assert task.status == "running"


def test_client_poll_heartbeat_returns_age(tmp_data_dir):
    """IPCClient.poll_heartbeat returns seconds since last beat."""
    client = IPCClient()
    assert client.poll_heartbeat() is None  # no heartbeat yet
    write_heartbeat(now=1000.0)
    age = client.poll_heartbeat()  # uses time.time()
    assert age is not None and age >= 0

