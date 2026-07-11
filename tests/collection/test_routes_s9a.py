"""S9a — resume→control (L01), post-close safety (L12), progress endpoint,
worker spawner app-state (L13), and cli serve wiring.

Uses the full create_app() + TestClient (startup runs watchdog + relay, but
WORKER_AUTOSPAWN defaults off so no real worker is spawned). IPC submit is
monkeypatched so resume-path B (re-submit) is observable without a worker.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient


# ─── fixtures (mirror test_routes_collection) ─────────────────────────────

@pytest.fixture(autouse=True)
def tmp_data_dir(monkeypatch, tmp_path):
    td = tmp_path / "data"
    for sub in ["logs", "ipc/requests", "ipc/results", "ipc/progress",
                "ipc/control/cancel", "collection/profiles", "collection/debug"]:
        (td / sub).mkdir(parents=True, exist_ok=True)
    import config
    importlib.reload(config)
    try:
        import semilabs_hone.core.models.db as db_mod
        db_mod.reset_engine()
    except Exception:
        pass
    try:
        import semilabs_hone.modules.collection.scrapers.registry as reg_mod
        reg_mod._registry_cache = None
    except Exception:
        pass
    yield td


@pytest.fixture
def db_session(tmp_data_dir):
    from semilabs_hone.core.models.db import init_db, get_session, reset_engine, get_engine, Base
    init_db()
    sess = get_session()
    try:
        yield sess
    finally:
        sess.close()
        engine = get_engine()
        Base.metadata.drop_all(engine)
        reset_engine()


@pytest.fixture
def app(tmp_data_dir):
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _fake_ipc(monkeypatch):
    """Stub tasks _ipc_client so submit records; returns (cls, IPCRequest)."""
    from semilabs_hone.modules.collection.routes import tasks as tsk
    from semilabs_hone.core.ipc.protocol import IPCRequest

    submitted: list = []

    class _FakeClient:
        def submit(self, req):
            submitted.append(req)
            return None

    monkeypatch.setattr(tsk, "_ipc_client", lambda: (_FakeClient, IPCRequest))
    return submitted


def _make_task(db_session, *, status="need_human", request_id="rid-x"):
    from semilabs_hone.core.models.task import CollectionTask
    t = CollectionTask(account_id=1, platform="xiaohongshu", status=status,
                      max_posts_per_keyword=5, sort_type="general",
                      download_images=False, collect_comments=True,
                      request_id=request_id)
    db_session.add(t)
    db_session.commit()
    return t.id


# ─── L01: resume → control file (need_human path) ────────────────────────

class TestResumeControlFile:
    def test_need_human_resume_writes_control_file(self, client, db_session, monkeypatch):
        """need_human + request_id → POST resume writes control/ctrl_<rid>.json."""
        rid = "ctrl-rid-1"
        tid = _make_task(db_session, status="need_human", request_id=rid)
        submitted = _fake_ipc(monkeypatch)  # path B should NOT submit a new request

        from semilabs_hone.core.ipc.paths import control_path
        import json

        resp = client.post(f"/api/tasks/{tid}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resumed"
        assert body["request_id"] == rid
        # control file written with action:resume (worker reads-after-burns).
        p = control_path(rid)
        # The worker isn't running, so the file persists — assert it was written.
        # (If a worker were running it would burn it; here we just check it existed
        # at least momentarily — but since no worker, it should still be on disk.)
        assert p.exists(), "control/ctrl_<rid>.json must be written for need_human resume"
        assert json.loads(p.read_text())["action"] == "resume"
        # Path A: no new IPC request submitted.
        assert submitted == []

    def test_paused_resume_submits_new_request(self, client, db_session, monkeypatch):
        """paused (worker likely dead) → re-submit a fresh scrape_task request."""
        tid = _make_task(db_session, status="paused", request_id="paused-rid")
        submitted = _fake_ipc(monkeypatch)

        resp = client.post(f"/api/tasks/{tid}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert len(submitted) == 1
        assert submitted[0].op == "scrape_task"
        assert submitted[0].payload.get("resume") is True

    def test_resume_missing_task_404(self, client, monkeypatch):
        _fake_ipc(monkeypatch)
        resp = client.post("/api/tasks/nonexistent-uuid/resume")
        assert resp.status_code == 404

    def test_resume_conflict_when_another_running(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        from semilabs_hone.core.models.task import CollectionTask
        # A different task is running.
        other = CollectionTask(account_id=2, platform="xiaohongshu", status="running",
                               max_posts_per_keyword=3)
        db_session.add(other)
        db_session.commit()
        tid = _make_task(db_session, status="paused", request_id="rid-c")
        resp = client.post(f"/api/tasks/{tid}/resume")
        assert resp.status_code == 409


# ─── L12: post-close safety (no DetachedInstanceError) ─────────────────────

class TestResumePostCloseSafe:
    def test_paused_resume_no_detached_instance_error(self, client, db_session, monkeypatch):
        """Resuming a paused task must not raise DetachedInstanceError (L12).

        Pre-S9a the handler accessed task.account_id/platform/... after
        sess.close(); commit's expire_on_commit made those raise. Now fields are
        captured to locals before close.
        """
        _fake_ipc(monkeypatch)
        tid = _make_task(db_session, status="paused", request_id="rid-l12")
        resp = client.post(f"/api/tasks/{tid}/resume")
        # 200 — no 500 (DetachedInstanceError would surface as a 500 or error JSON).
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ─── progress endpoint (附带) ──────────────────────────────────────────────

class TestProgressEndpoint:
    def test_progress_found_returns_json(self, client, db_session):
        from semilabs_hone.core.ipc.paths import atomic_write_json, progress_path
        rid = "prog-ep"
        tid = _make_task(db_session, status="running", request_id=rid)
        atomic_write_json(progress_path(rid), {
            "request_id": rid, "message": "phase3_detail",
            "data": {"platform_id": "abc"}, "updated_at": 1.0,
        })
        resp = client.get(f"/api/tasks/{tid}/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["message"] == "phase3_detail"

    def test_progress_no_file_404(self, client, db_session):
        rid = "prog-none"
        tid = _make_task(db_session, status="running", request_id=rid)
        resp = client.get(f"/api/tasks/{tid}/progress")
        assert resp.status_code == 404

    def test_progress_no_request_id_404(self, client, db_session):
        # Task with no request_id → 404 (no progress correlation possible).
        tid = _make_task(db_session, status="pending", request_id=None)
        resp = client.get(f"/api/tasks/{tid}/progress")
        assert resp.status_code == 404


# ─── L13: worker spawner app-state + cli serve ────────────────────────────

class TestWorkerSpawnerAppState:
    def test_autospawn_off_no_spawner_on_state(self, client):
        """WORKER_AUTOSPAWN defaults off → app.state has no worker_spawner."""
        assert getattr(client.app.state, "worker_spawner", None) is None


class TestCliServe:
    def test_serve_invokes_uvicorn_and_enables_autospawn(self, monkeypatch):
        """`serve` wires uvicorn.run + sets SEMILABS_WORKER_AUTOSPAWN=1."""
        import os
        import semilabs_hone.cli as cli_mod

        called = {"n": 0}
        def _fake_run(app, **kw):
            called["n"] += 1
            called["host"] = kw.get("host")
            called["port"] = kw.get("port")
            # Don't actually start the server in tests.
            return None
        monkeypatch.setattr(cli_mod.uvicorn, "run", _fake_run, raising=False) \
            if hasattr(cli_mod, "uvicorn") else None
        # cli imports uvicorn inside serve(); patch the installed module.
        import uvicorn
        monkeypatch.setattr(uvicorn, "run", _fake_run)

        monkeypatch.delenv("SEMILABS_WORKER_AUTOSPAWN", raising=False)
        rc = cli_mod.main(["serve", "--host", "127.0.0.1", "--port", "8530"])
        assert rc == 0
        assert called["n"] == 1
        assert called["host"] == "127.0.0.1"
        assert called["port"] == 8530
        assert os.environ.get("SEMILABS_WORKER_AUTOSPAWN") == "1"

    def test_worker_requires_account(self):
        import semilabs_hone.cli as cli_mod
        rc = cli_mod.main(["worker", "--module", "collection"])
        assert rc == 1  # no --account → rejected
