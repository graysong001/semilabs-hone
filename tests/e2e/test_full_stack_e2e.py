"""Full-stack E2E: HTTP → IPC files → real worker subprocess → WebSocket.

This is the whole product running: the real FastAPI app submits a task,
the real supervisor spawns the real worker process, that worker launches
its own real Chrome, scrapes the real local site, writes the real result
file, and the real tracker relays progress to a real WebSocket client.

Only run where Chrome exists. Slower than the in-process E2E (a browser
plus a Python process start up), so it lives in its own module.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config

pytestmark = pytest.mark.skipif(
    not Path(config.CHROME_BIN).exists(),
    reason=f"real Chrome not found at {config.CHROME_BIN}",
)

TASK_TIMEOUT = 180.0
POLL_INTERVAL = 1.0


@pytest.fixture
def full_stack(tmp_data_dir, monkeypatch, no_worker_launch):
    """Real worker launching enabled, and the child sees the same data dir."""
    from semilabs_hone.core.ipc import worker_spawner

    # The worker is a separate process: it reads config from the environment.
    monkeypatch.setenv("SEMILABS_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("SEMILABS_QUIET_HOURS", "off")
    monkeypatch.setenv("SEMILABS_WORKER_AUTOSPAWN", "1")
    # Warmup would browse the public internet from the child; not in a test.
    monkeypatch.setenv("SEMILABS_WARMUP_PAGES", "off")
    # Real rhythm delays are 30-180s; a test must not wait them out.
    monkeypatch.setenv("SEMILABS_NOTE_DELAY", "0-0")
    monkeypatch.setenv("SEMILABS_KEYWORD_DELAY", "0-0")
    monkeypatch.setattr(config, "QUIET_HOURS", None, raising=False)
    monkeypatch.setattr(config, "WARMUP_PAGES", None, raising=False)
    monkeypatch.setattr(config, "WORKER_AUTOSPAWN", True, raising=False)
    # Undo the global "never launch a worker" guard for this test only.
    original = getattr(no_worker_launch, "original", None)
    if original is not None:
        monkeypatch.setattr(worker_spawner, "ensure_worker", original)

    yield
    worker_spawner.shutdown_all()


def _active_account(db_session, platform: str) -> int:
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.modules.collection.anti_detect.fingerprint import assign_fingerprint
    from semilabs_hone.modules.collection.browser.profile import ensure_profile

    fp = assign_fingerprint()
    acct = Account(
        platform=platform, nickname="fullstack", status="active",
        viewport_w=fp.viewport["width"], viewport_h=fp.viewport["height"],
        color_scheme=fp.color_scheme, timezone=fp.timezone, locale=fp.locale,
    )
    db_session.add(acct)
    db_session.commit()
    acct.profile_dir = str(ensure_profile(acct.id))
    db_session.commit()
    return acct.id


def _wait_for_task(db_session, task_id: int, statuses=("completed", "failed")) -> str:
    """Poll the shared SQLite until the worker reports a terminal status."""
    from semilabs_hone.core.models.task import CollectionTask

    deadline = time.monotonic() + TASK_TIMEOUT
    while time.monotonic() < deadline:
        db_session.expire_all()
        task = db_session.query(CollectionTask).filter(CollectionTask.id == task_id).one()
        if task.status in statuses:
            return task.status
        time.sleep(POLL_INTERVAL)
    return "timeout"


def _worker_log(tmp_data_dir: Path) -> str:
    logs = sorted((tmp_data_dir / "logs").glob("worker_*.log"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in logs)


def test_task_runs_through_a_real_worker_process(
    db_session, tmp_data_dir, localtest_platform, full_stack
):
    """POST a task, let the real worker do it, watch it over the real WS."""
    from semilabs_hone.core.models.comment import CollectionComment
    from semilabs_hone.core.models.db import init_db
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.ui.app import create_app

    account_id = _active_account(db_session, localtest_platform)
    init_db()

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as ws:
            created = client.post("/api/tasks", data={
                "account_id": str(account_id),
                "platform": localtest_platform,
                "target_value": "咖啡",
                "sort": "general",
                "expected_count": "5",
                "download_images": "false",
                "collect_comments": "true",
            }).json()
            assert created["ok"] is True, created

            status = _wait_for_task(db_session, created["task_id"])
            assert status == "completed", (
                f"task ended as {status}\n--- worker log ---\n{_worker_log(tmp_data_dir)}"
            )

            # The tracker relayed the worker's progress over the real socket.
            # Progress is a 1 Hz sample of an overwritten snapshot file, not an
            # event log, so a fast run legitimately skips intermediate steps.
            messages = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                messages.append(ws.receive_json())
                if messages[-1].get("type") == "task_completed":
                    break
            types = [m.get("type") for m in messages]
            assert "task_completed" in types, messages
            assert any(t == "progress" for t in types), messages
            assert all(
                m.get("task_id") == created["task_id"] for m in messages
            ), messages

        # Data landed in the shared database and is readable over HTTP.
        posts = db_session.query(CollectionItem).filter(CollectionItem.task_id == created["task_id"]).all()
        assert {p.platform_id for p in posts} == {"note-1", "note-2"}
        assert db_session.query(CollectionComment).count() == 3

        listing = client.get("/posts")
        assert "手冲咖啡入门" in listing.text

        progress = client.get(f"/api/tasks/{created['task_id']}/progress").json()
        assert progress["ok"] is True
        assert progress["data"]["posts_scraped"] == 2


def test_cancel_reaches_the_worker_and_stops_the_task(
    db_session, tmp_data_dir, localtest_platform, full_stack
):
    """The cancel sentinel written by the web process ends the worker's run.

    The result file itself is consumed by the tracker, so the observable
    contract is: the task stays cancelled, the UI is told, and nothing was
    scraped afterwards.
    """
    from semilabs_hone.core.ipc.paths import cancel_sentinel
    from semilabs_hone.core.models.db import init_db
    from semilabs_hone.core.models.post import CollectionItem
    from semilabs_hone.core.models.task import CollectionTask
    from semilabs_hone.core.ui.app import create_app

    account_id = _active_account(db_session, localtest_platform)
    init_db()

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as ws:
            created = client.post("/api/tasks", data={
                "account_id": str(account_id),
                "platform": localtest_platform,
                "target_value": "咖啡",
                "sort": "general",
                "expected_count": "5",
                "download_images": "false",
                "collect_comments": "true",
            }).json()

            # Cancel immediately: the worker is still starting Chrome.
            cancelled = client.post(f"/api/tasks/{created['task_id']}/cancel").json()
            assert cancelled["ok"] is True
            assert cancel_sentinel(created["request_id"]).exists()

            db_session.expire_all()
            assert db_session.query(CollectionTask).filter(
                CollectionTask.id == created["task_id"]
            ).one().status == "cancelled"

            # The worker must honour the sentinel: whatever it reports, it
            # must not scrape a cancelled task.
            terminal = None
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                message = ws.receive_json()
                if message.get("request_id") != created["request_id"]:
                    continue  # leftovers from another request
                if message.get("type") in ("warn", "error", "task_completed"):
                    terminal = message
                    break

        db_session.expire_all()
        final_status = db_session.query(CollectionTask).filter(
            CollectionTask.id == created["task_id"]
        ).one().status

    log = _worker_log(tmp_data_dir)
    assert terminal is not None, log
    assert terminal["type"] == "warn", (terminal, log)
    assert "取消" in terminal["message"], terminal
    assert final_status == "cancelled", log
    assert db_session.query(CollectionItem).filter(
        CollectionItem.task_id == created["task_id"]
    ).count() == 0, log
