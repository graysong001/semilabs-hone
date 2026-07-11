"""PRD §8.3 — 跨进程通信与底层自愈验收 (IPC & Core Resilience).

BDD acceptance tests for scenarios 3.1 (脏文件容错) and 3.2 (读后即焚).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from semilabs_hone.core.ipc.client import IPCClient
from semilabs_hone.core.ipc.paths import (
    atomic_write_json,
    control_path,
    read_json_if_exists,
    request_path,
)
from semilabs_hone.core.ipc.protocol import IPCRequest
from semilabs_hone.core.ipc.server import serve_worker


# ─── 场景 3.1：IPC 脏文件与格式错误容错 ────────────────────────────────

async def _echo_handler(payload, progress_cb):
    return {"status": "ok", "echo": payload}


class TestScenario31CorruptFileTolerance:
    """PRD §8.3 场景 3.1.

    Given Web 进程向 control/ 写入了一个非标准 JSON 文件（如意外截断的
          {"action": "pau）.
    When  Worker 轮询读取该文件.
    Then  Worker 必须 try-except json.JSONDecodeError，记录报错日志，立刻删除
          该坏损文件，并继续维持 Main Loop 运行，绝对不能导致 Worker 崩溃.
    """

    async def test_corrupt_control_json_burned_not_raised(self, tmp_data_dir):
        """A truncated control JSON is burned + None returned, never raises.

        PRD 3.1 Then: try-except JSONDecodeError + 立刻删除 + 不崩溃.
        """
        from semilabs_hone.core.ipc import server

        p = control_path("badctrl")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"action": "pau', encoding="utf-8")  # truncated

        # When: worker reads it.
        action = server._consume_control("badctrl")

        # Then: no raise, file burned.
        assert action is None
        assert not p.exists()

    async def test_corrupt_request_keeps_main_loop_alive(self, tmp_data_dir):
        """A corrupt request file is burned; a good request right after still processes.

        PRD 3.1 Then: 继续维持 Main Loop 运行，绝对不能导致整个 Worker 进程崩溃.
        """
        bad = request_path("badjson")
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text('{"request_id": "badjson", "module": "bdd", "op"', encoding="utf-8")

        # A good request right after — proves the loop survived the bad one.
        client = IPCClient()
        client.submit(IPCRequest(request_id="good_bdd", module="bdd", op="echo", payload={}))

        task = asyncio.create_task(serve_worker("bdd", {"echo": _echo_handler}, poll_interval=0.1))
        try:
            await asyncio.sleep(0.5)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Then: bad file burned, good request processed → loop did not crash.
        assert not bad.exists()
        res = await client.wait_result("good_bdd", timeout=2)
        assert res.status == "ok"


# ─── 场景 3.2：IPC 指令"读后即焚"验证 ──────────────────────────────────

class TestScenario32ReadAfterBurn:
    """PRD §8.3 场景 3.2.

    Given 用户点击了【暂停】按钮，生成了 cmd_pause_<id>.json.
    When  Worker 轮询到该文件并执行暂停动作.
    Then  该 JSON 文件必须在执行挂起的同一毫秒内从文件系统中被 os.remove().
          如果文件残留导致 Worker 解除暂停后又立刻暂停，视为 Bug.
    """

    async def test_control_file_removed_on_consume(self, tmp_data_dir):
        """A control directive is os.remove()'d the instant it is consumed.

        PRD 3.2 Then: 文件必须在执行挂起的同一毫秒内被 os.remove().
        """
        from semilabs_hone.core.ipc import server

        atomic_write_json(control_path("pause1"), {"action": "pause"})
        assert control_path("pause1").exists()

        action = server._consume_control("pause1")

        assert action == "pause"
        # Read-after-burn: the file is gone immediately after consumption.
        assert not control_path("pause1").exists()

    async def test_request_file_removed_after_pickup(self, tmp_data_dir):
        """A request file is burned after pickup (no re-pickup → no double-run).

        PRD 3.2 Then (no residue): a leftover request would cause a double-run; the
        server burns it on pickup so the same request can never be processed twice.
        """
        client = IPCClient()
        client.submit(IPCRequest(request_id="pickonce", module="bdd2", op="echo", payload={}))
        req_path = request_path("pickonce")
        assert req_path.exists()

        task = asyncio.create_task(serve_worker("bdd2", {"echo": _echo_handler}, poll_interval=0.1))
        try:
            await asyncio.sleep(0.4)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Burned on pickup — no residue.
        assert not req_path.exists()
        res = await client.wait_result("pickonce", timeout=2)
        assert res.status == "ok"
