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
    """Stub accounts _ipc so submit is a no-op; returns (cls, IPCRequest)."""
    from semilabs_hone.modules.collection.routes import accounts as acc
    from semilabs_hone.core.ipc.protocol import IPCRequest

    class _FakeClient:
        def submit(self, req):
            return None

    monkeypatch.setattr(acc, "_ipc", lambda: (_FakeClient, IPCRequest))


def _seed_account(db_session, *, platform="xiaohongshu", remark="acc"):
    from semilabs_hone.core.models.account import Account
    a = Account(platform=platform, remark=remark)
    db_session.add(a); db_session.commit()
    return a.id


# ─── accounts routes ─────────────────────────────────────────────────────

class TestAccountsRoutes:
    def test_get_accounts_page(self, client):
        resp = client.get("/accounts")
        assert resp.status_code == 200

    def test_create_account_redirects_and_shows_remark(self, client):
        # v2 S10: create 是整页表单 → 303 重定向; remark 必填（原 nickname）。
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu", "remark": "n1"},
                           follow_redirects=False)
        assert resp.status_code == 303
        page = client.get("/accounts")
        assert "n1" in page.text

    def test_create_account_requires_remark(self, client):
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu"},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "error=remark_required" in resp.headers["location"]

    def test_create_account_legacy_nickname_field_compat(self, client):
        # 兼容旧 form 的 nickname 字段（映射为 remark）。
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu", "nickname": "old"},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "error" not in resp.headers.get("location", "")

    def test_create_account_assigns_fingerprint_and_profile(self, client, db_session):
        # F6: creation draws the one-account-one-fixed fingerprint + profile dir.
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu", "remark": "fp"},
                           follow_redirects=False)
        assert resp.status_code == 303
        from semilabs_hone.core.models.account import Account
        acct = db_session.query(Account).filter(Account.remark == "fp").first()
        assert acct is not None
        assert acct.profile_dir
        assert acct.viewport_w > 0 and acct.viewport_h > 0

    def test_delete_existing_account(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.delete(f"/api/accounts/{aid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        from semilabs_hone.core.models.account import Account
        assert db_session.query(Account).filter(Account.id == aid).first() is None

    def test_delete_missing_account_404(self, client):
        # v2 S10: JSON 契约，缺失 → 404。
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

    def test_login_unknown_account_404_with_hint(self, client):
        resp = client.post("/api/accounts/999999/login")
        assert resp.status_code == 404
        assert resp.json()["fix_hint"]

    def test_import_cookies_valid_json(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(
            "/api/accounts/import-cookies",
            data={"account_id": aid,
                  "cookies": '[{"name":"sid","value":"x","domain":".xiaohongshu.com"}]'})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["status"] == "submitted"

    def test_import_cookies_invalid_json(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(
            "/api/accounts/import-cookies",
            data={"account_id": aid, "cookies": "not json"})
        # Invalid JSON → 400 with a fix_hint (never silently submitted).
        assert resp.status_code == 400
        assert resp.json()["fix_hint"]

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
            task = CollectionTask(platform="xiaohongshu",
                                  status=status, expected_count=10,
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
            task = CollectionTask(platform="xiaohongshu",
                                  status=status, expected_count=10)
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
        t = CollectionTask(platform="xiaohongshu",
                           status=status, expected_count=10,
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
        other = CollectionTask(platform="xiaohongshu",
                               status="running", expected_count=10)
        db_session.add(other); db_session.commit()
        tid = self._make_task(db_session, status="paused")
        resp = client.post(f"/api/tasks/{tid}/resume")
        assert resp.status_code == 409

    def test_resume_missing_task_404(self, client, db_session, monkeypatch):
        self._fake_tasks_ipc(monkeypatch)
        resp = client.post("/api/tasks/nonexistent/resume")
        assert resp.status_code == 404


class TestTasksPauseDeleteActivate:
    """v2 任务大厅新端点: pause / delete / activate-browser (Stage 3)."""

    def _make_task(self, db_session, status="running"):
        from semilabs_hone.core.models.task import CollectionTask
        t = CollectionTask(platform="xiaohongshu",
                           status=status, expected_count=10,
                           request_id="req-pda")
        db_session.add(t); db_session.commit()
        return t.id

    # --- pause ---
    def test_pause_running_ok(self, client, db_session):
        tid = self._make_task(db_session, status="running")
        resp = client.post(f"/api/tasks/{tid}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"
        # control file written so the live worker stops at its next step boundary
        from semilabs_hone.core.ipc import paths as ipc_paths
        ctrl = ipc_paths.read_json_if_exists(ipc_paths.control_path("req-pda"))
        assert ctrl == {"action": "pause"}

    def test_pause_not_running_409(self, client, db_session):
        tid = self._make_task(db_session, status="paused")
        assert client.post(f"/api/tasks/{tid}/pause").status_code == 409

    def test_pause_missing_404(self, client):
        assert client.post("/api/tasks/nope/pause").status_code == 404

    # --- delete ---
    def test_delete_completed_ok(self, client, db_session):
        tid = self._make_task(db_session, status="completed")
        resp = client.delete(f"/api/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        from semilabs_hone.core.models.task import CollectionTask
        assert db_session.query(CollectionTask).filter(
            CollectionTask.id == tid).first() is None

    def test_delete_running_409(self, client, db_session):
        tid = self._make_task(db_session, status="running")
        assert client.delete(f"/api/tasks/{tid}").status_code == 409

    def test_delete_missing_404(self, client):
        assert client.delete("/api/tasks/nope").status_code == 404

    # --- activate-browser ---
    def test_activate_browser_missing_404(self, client):
        assert client.post("/api/tasks/nope/activate-browser").status_code == 404

    def test_activate_browser_not_need_human_409(self, client, db_session):
        tid = self._make_task(db_session, status="running")
        assert client.post(f"/api/tasks/{tid}/activate-browser").status_code == 409

    def test_activate_browser_macos_ok(self, client, db_session, monkeypatch):
        import subprocess as sp
        import sys
        monkeypatch.setattr(sp, "run", lambda *a, **k: None)
        monkeypatch.setattr(sys, "platform", "darwin")
        tid = self._make_task(db_session, status="need_human")
        resp = client.post(f"/api/tasks/{tid}/activate-browser")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_activate_browser_non_macos_501(self, client, db_session, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "linux")
        tid = self._make_task(db_session, status="need_human")
        resp = client.post(f"/api/tasks/{tid}/activate-browser")
        assert resp.status_code == 501
        assert resp.json()["ok"] is False


# ─── accounts v2 S10 endpoints ───────────────────────────────────────────

class TestAccountsV2Endpoints:
    """v2 S10 三标识 + 行级编辑端点（Stage 5 移植）。"""

    def test_list_accounts_json(self, client, db_session):
        aid = _seed_account(db_session, remark="json-acc")
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        mine = [r for r in rows if r["id"] == aid]
        assert mine and mine[0]["remark"] == "json-acc"
        assert "platform_user_id" in mine[0] and "platform_nickname" in mine[0]

    def test_get_account_json_and_404(self, client, db_session):
        aid = _seed_account(db_session, remark="detail")
        resp = client.get(f"/api/accounts/{aid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["remark"] == "detail"
        assert client.get("/api/accounts/999999").status_code == 404

    def test_edit_dialog_renders(self, client, db_session):
        aid = _seed_account(db_session, remark="dlg")
        resp = client.get(f"/api/accounts/{aid}/edit")
        assert resp.status_code == 200
        assert "account-edit-dialog" in resp.text
        assert 'value="dlg"' in resp.text
        assert resp.headers["content-type"].startswith("text/html")
        assert client.get("/api/accounts/999999/edit").status_code == 404

    def test_edit_post_remark_only(self, client, db_session):
        aid = _seed_account(db_session, remark="before")
        resp = client.post(f"/api/accounts/{aid}/edit", data={"remark": "after"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["remark_changed"] is True
        from semilabs_hone.core.models.account import Account
        acct = db_session.query(Account).filter(Account.id == aid).first()
        assert acct.remark == "after"

    def test_edit_post_invalid_cookies_400(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.post(f"/api/accounts/{aid}/edit",
                           data={"cookies": '[{"name":"x"}]'})  # 缺 value/domain
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_edit_post_with_cookies_saves_file_and_submits(
            self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        payload = '[{"name":"sid","value":"v","domain":".xiaohongshu.com"}]'
        resp = client.post(f"/api/accounts/{aid}/edit", data={"cookies": payload})
        assert resp.status_code == 200
        body = resp.json()
        assert body["cookie_saved"] is True and body["cookie_submitted"] is True
        from semilabs_hone.modules.collection.browser.profile import profile_dir_for
        assert (profile_dir_for(aid) / "cookies.json").exists()

    def test_update_cookie_dialog_renders(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.get(f"/api/accounts/{aid}/update-cookie-dialog")
        assert resp.status_code == 200
        assert "更新 Cookie" in resp.text
        assert client.get("/api/accounts/999999/update-cookie-dialog").status_code == 404

    def test_update_cookie_valid_submits(self, client, db_session, monkeypatch):
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session)
        resp = client.post(
            f"/api/accounts/{aid}/update-cookie",
            data={"cookies": '[{"name":"sid","value":"v","domain":".xiaohongshu.com"}]'})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["status"] == "submitted"

    def test_update_cookie_invalid_400(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.post(f"/api/accounts/{aid}/update-cookie",
                           data={"cookies": "[]"})
        assert resp.status_code == 400

    def test_put_account_remark(self, client, db_session):
        aid = _seed_account(db_session, remark="put-before")
        resp = client.put(f"/api/accounts/{aid}", json={"remark": "put-after"})
        assert resp.status_code == 200
        assert resp.json()["remark"] == "put-after"
        from semilabs_hone.core.models.account import Account
        acct = db_session.query(Account).filter(Account.id == aid).first()
        assert acct.remark == "put-after"

    def test_put_account_remark_required(self, client, db_session):
        aid = _seed_account(db_session)
        resp = client.put(f"/api/accounts/{aid}", json={"remark": "  "})
        assert resp.status_code == 400
        assert client.put("/api/accounts/999999", json={"remark": "x"}).status_code == 404

    def test_delete_blocked_by_running_task_same_platform(self, client, db_session):
        # v2 S10 守卫的平台化适配：PRD 无 task→account FK，同平台 running → 409。
        from semilabs_hone.core.models.task import CollectionTask
        aid = _seed_account(db_session, platform="xiaohongshu")
        task = CollectionTask(platform="xiaohongshu", status="running",
                              expected_count=10)
        db_session.add(task); db_session.commit()
        resp = client.delete(f"/api/accounts/{aid}")
        assert resp.status_code == 409
        db_session.rollback()
