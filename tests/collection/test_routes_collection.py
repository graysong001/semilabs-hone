"""Collection routes coverage — accounts/posts/tasks endpoints + helpers.

Covers accounts CRUD + login/validate/import IPC routes, posts list/detail/
comments-fragment, tasks badge/actions HTML helpers + not-found + cancel/resume.
Uses the full app (create_app) + TestClient; IPC submit is monkeypatched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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
    """Stub accounts _ipc_client so submit is a no-op; returns (cls, IPCRequest)."""
    from semilabs_hone.modules.collection.routes import accounts as acc
    from semilabs_hone.core.ipc.protocol import IPCRequest

    class _FakeClient:
        def submit(self, req):
            return None

    monkeypatch.setattr(acc, "_ipc_client", lambda: (_FakeClient, IPCRequest))


def _seed_account(db_session, *, platform="xiaohongshu", nickname="acc"):
    from semilabs_hone.core.models.account import Account
    a = Account(platform=platform, nickname=nickname)
    db_session.add(a); db_session.commit()
    return a.id


# ─── accounts routes ─────────────────────────────────────────────────────

class TestAccountsRoutes:
    def test_get_accounts_page(self, client):
        resp = client.get("/accounts")
        assert resp.status_code == 200

    def test_create_account_redirects(self, client):
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu", "nickname": "n1"},
                           follow_redirects=False)
        assert resp.status_code == 303

    def test_delete_existing_account(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.delete(f"/api/accounts/{aid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_missing_account_404(self, client):
        resp = client.delete("/api/accounts/999999")
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    def test_login_account_submits_ipc(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(f"/api/accounts/{aid}/login")
        assert resp.status_code == 200
        body = resp.json()
        assert "request_id" in body
        assert body["status"] == "submitted"

    def test_import_cookies_valid_json(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(
            "/api/accounts/import-cookies",
            data={"account_id": aid, "cookies": '[{"name":"sid"}]'},
            follow_redirects=False)
        assert resp.status_code == 303

    def test_import_cookies_invalid_json(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(
            "/api/accounts/import-cookies",
            data={"account_id": aid, "cookies": "not json"},
            follow_redirects=False)
        # Invalid JSON → empty cookies list, still submitted → redirect.
        assert resp.status_code == 303

    def test_validate_account_submits_ipc(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(f"/api/accounts/{aid}/validate")
        assert resp.status_code == 200
        assert "request_id" in resp.json()


# ─── posts routes ─────────────────────────────────────────────────────────

def _seed_item(db_session, *, platform_id, title="t", content="c", likes=10,
               task_id=None):
    from semilabs_hone.core.models import repository as repo
    return repo.upsert_item(
        db_session, task_id=task_id, platform="xiaohongshu",
        platform_id=platform_id, url=f"https://x/{platform_id}",
        title=title, content_text=content, author_name="A",
        metrics={"likes": likes, "comments_count": 0}, publish_time="2026-07-08 14:00:00")


def _seed_comment(db_session, *, item_id, platform_comment_id, author="u",
                  content="hi", likes=5):
    from semilabs_hone.core.models import repository as repo
    return repo.upsert_comment(
        db_session, item_id=item_id, platform_comment_id=platform_comment_id,
        author_name=author, content_text=content, like_count=likes)


class TestPostsRoutes:
    def test_get_posts_page(self, client):
        resp = client.get("/posts")
        assert resp.status_code == 200

    def test_get_posts_with_platform_filter(self, client, db_session):
        _seed_item(db_session, platform_id="p1")
        resp = client.get("/posts?platform=xiaohongshu")
        assert resp.status_code == 200

    def test_item_comments_fragment_with_comments(self, client, db_session):
        item = _seed_item(db_session, platform_id="c1")
        _seed_comment(db_session, item_id=item.id, platform_comment_id="cm1",
                      author="评论者", content="好文", likes=9)
        resp = client.get(f"/api/items/{item.id}/comments")
        assert resp.status_code == 200
        assert "评论者" in resp.text
        assert "好文" in resp.text

    def test_item_comments_fragment_empty_shows_muted(self, client, db_session):
        item = _seed_item(db_session, platform_id="c2")
        resp = client.get(f"/api/items/{item.id}/comments")
        assert resp.status_code == 200
        assert "暂无评论" in resp.text

    def test_post_detail_found(self, client, db_session):
        item = _seed_item(db_session, platform_id="d1", title="详情标题")
        resp = client.get(f"/posts/{item.id}")
        assert resp.status_code == 200
        assert "详情标题" in resp.text

    def test_post_detail_not_found_404(self, client):
        resp = client.get("/posts/nonexistent-uuid")
        assert resp.status_code == 404


# ─── tasks helpers + endpoints ───────────────────────────────────────────

class TestTasksHelpers:
    def test_badge_html_per_status(self, db_session, tmp_data_dir):
        from semilabs_hone.modules.collection.routes import tasks as t
        from semilabs_hone.core.models.task import CollectionTask
        for status in ("pending", "running", "paused", "need_human",
                       "completed", "error"):
            task = CollectionTask(account_id=1, platform="xiaohongshu",
                                  status=status, max_posts_per_keyword=10,
                                  error_msg="boom" if status == "error" else None)
            db_session.add(task)
            db_session.commit()
            html = t._badge_html(task)
            assert isinstance(html, str) and html
            db_session.rollback()

    def test_actions_html_per_status(self, db_session, tmp_data_dir):
        from semilabs_hone.modules.collection.routes import tasks as t
        from semilabs_hone.core.models.task import CollectionTask
        for status in ("running", "need_human", "paused", "completed", "error"):
            task = CollectionTask(account_id=1, platform="xiaohongshu",
                                  status=status, max_posts_per_keyword=10)
            db_session.add(task)
            db_session.commit()
            html = t._actions_html(task)
            assert isinstance(html, str) and html
            db_session.rollback()


class TestTasksEndpointsNotFound:
    def test_status_not_found(self, client):
        resp = client.get("/api/tasks/nope/status")
        # not-found returns 404 or empty; either is acceptable as long as no 500
        assert resp.status_code in (200, 404)

    def test_row_not_found(self, client):
        resp = client.get("/api/tasks/nope/row")
        assert resp.status_code in (200, 404)

    def test_actions_not_found(self, client):
        resp = client.get("/api/tasks/nope/actions")
        assert resp.status_code in (200, 404)


class TestTasksCancelResume:
    def _make_task(self, db_session, status="running"):
        from semilabs_hone.core.models.task import CollectionTask
        t = CollectionTask(account_id=1, platform="xiaohongshu",
                           status=status, max_posts_per_keyword=10,
                           request_id="req-test")
        db_session.add(t); db_session.commit()
        return t.id

    def _fake_tasks_ipc(self, monkeypatch):
        from semilabs_hone.modules.collection.routes import tasks as t
        from semilabs_hone.core.ipc.protocol import IPCRequest

        class _FakeClient:
            def submit(self, req):
                return None

            def cancel(self, key):
                return None

        monkeypatch.setattr(t, "_ipc_client", lambda: (_FakeClient, IPCRequest))

    def test_cancel_running_task(self, client, db_session, monkeypatch):
        self._fake_tasks_ipc(monkeypatch)
        tid = self._make_task(db_session, status="running")
        resp = client.post(f"/api/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_resume_conflict_when_another_running(self, client, db_session, monkeypatch):
        self._fake_tasks_ipc(monkeypatch)
        from semilabs_hone.core.models.task import CollectionTask
        # Another running task.
        other = CollectionTask(account_id=1, platform="xiaohongshu",
                               status="running", max_posts_per_keyword=10)
        db_session.add(other); db_session.commit()
        tid = self._make_task(db_session, status="paused")
        resp = client.post(f"/api/tasks/{tid}/resume")
        assert resp.status_code == 409

    def test_resume_missing_task_404(self, client, db_session, monkeypatch):
        self._fake_tasks_ipc(monkeypatch)
        resp = client.post("/api/tasks/nonexistent/resume")
        assert resp.status_code == 404
