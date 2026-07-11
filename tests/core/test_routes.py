"""DM-04 Web shell tests.

Covers:
- GET / returns 200 (dashboard)
- Empty DB shows guidance card
- SkimError global handler -> JSON {error, category, fix_hint}
- WSManager broadcast -> message_buffer
Naming: test_<method>_<scenario>_<expected>
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from semilabs_hone.core.ui.ws import WSManager, ws_manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_data_dir):
    """Create app with tmp data dir; reset engine so startup uses patched config."""
    from semilabs_hone.core.models.db import reset_engine

    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    """TestClient (triggers startup -> init_db + setup_logger + manifest scan)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_get_dashboard_returns_200(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200


def test_get_dashboard_empty_shows_guidance(client: TestClient):
    """Empty DB should show the 'no accounts' guidance card."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "开始使用" in html or "尚未添加" in html


# ---------------------------------------------------------------------------
# SkimError global handler
# ---------------------------------------------------------------------------

def test_skimerror_handler_returns_json(client: TestClient):
    """Register a test route that raises SkimError, verify JSON response."""
    from fastapi import APIRouter
    from semilabs_hone.core.utils.retry import SkimError

    test_router = APIRouter()

    @test_router.get("/test_skim_error")
    async def raise_skim_error():
        raise SkimError("test error message", category="test_category", fix_hint="test fix hint")

    # Manually include the test router
    client.app.include_router(test_router)

    response = client.get("/test_skim_error")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "test error message"
    assert body["category"] == "test_category"
    assert body["fix_hint"] == "test fix hint"


def test_non_skimerror_not_handled(tmp_data_dir):
    """Non-SkimError exceptions should NOT be caught by our handler (500)."""
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    app = create_app()
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test_runtime_error")
    async def raise_runtime_error():
        raise RuntimeError("unexpected error")

    app.include_router(test_router)

    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/test_runtime_error")
    # FastAPI default for unhandled errors is 500
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# WSManager
# ---------------------------------------------------------------------------

def test_wsmanager_broadcast_in_buffer():
    """Broadcasting a message should add it to the message buffer."""
    mgr = WSManager()
    msg = {"type": "progress", "message": "test progress"}

    import asyncio
    asyncio.run(mgr.broadcast(msg))

    assert len(mgr.message_buffer) == 1
    assert mgr.message_buffer[0] == msg


def test_wsmanager_buffer_maxlen():
    """Buffer should respect maxlen=50."""
    mgr = WSManager()

    import asyncio
    for i in range(60):
        asyncio.run(mgr.broadcast({"type": "progress", "message": f"msg {i}"}))

    assert len(mgr.message_buffer) == 50
    # Oldest messages should have been evicted
    assert mgr.message_buffer[0]["message"] == "msg 10"


def test_wsmanager_connect_disconnect():
    """Connect adds ws to set, disconnect removes it."""
    mgr = WSManager()
    mock_ws = MagicMock()
    mock_ws.accept = AsyncMock()

    import asyncio

    async def _test():
        await mgr.connect(mock_ws)
        assert mock_ws in mgr.connections
        mock_ws.accept.assert_called_once()

        await mgr.disconnect(mock_ws)
        assert mock_ws not in mgr.connections

    asyncio.run(_test())


def test_wsmanager_connect_replays_buffer():
    """New connections should receive buffered messages."""
    mgr = WSManager()
    mock_ws = MagicMock()
    mock_ws.accept = AsyncMock()
    mock_ws.send_json = AsyncMock()
    # Pre-populate buffer
    mgr.message_buffer.append({"type": "progress", "message": "replayed"})

    import asyncio

    async def _test():
        await mgr.connect(mock_ws)
        assert mock_ws in mgr.connections
        # Should have called send_json for the buffered message
        assert mock_ws.send_json.call_count == 1
        assert mock_ws.send_json.call_args[0][0] == {"type": "progress", "message": "replayed"}

    asyncio.run(_test())


def test_wsmanager_module_singleton():
    """The module-level ws_manager should be a WSManager instance."""
    assert isinstance(ws_manager, WSManager)
    assert isinstance(ws_manager.message_buffer, deque)
    assert ws_manager.message_buffer.maxlen == 50


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def test_manifest_discovers_collection(app):
    """create_app startup should discover the collection module via manifest."""
    # The app has already run startup via TestClient
    from semilabs_hone.core.ui.app import _module_registry
    assert "collection" in _module_registry
    mod = _module_registry["collection"]
    assert mod["name"] == "Skim 采集"
    assert mod["module_id"] == "collection"
    assert mod["worker_entry"] == "semilabs_hone.modules.collection.browser.worker_main"


def test_manifest_empty_routes_no_error(app):
    """Empty ROUTES list should not cause errors during startup."""
    # If we get here without exception, the test passes
    # Verified by the app fixture creation above
    from semilabs_hone.core.ui.app import _module_registry
    assert "collection" in _module_registry


# ---------------------------------------------------------------------------
# Single-running concurrency lock (PRD §8.2 场景 2.2)
# ---------------------------------------------------------------------------

def _create_task_form(target_value: str) -> dict:
    """PRD §4.1.1 form (S6/T32 migration): task_type/target_value/expected_count."""
    return {
        "account_id": 0,
        "platform": "xiaohongshu",
        "task_type": "keyword_search",
        "target_value": target_value,
        "expected_count": 10,
        "sort": "general",
        "download_images": "false",
        "collect_comments": "false",
    }


def _get_task_statuses() -> dict:
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        tasks = sess.query(CollectionTask).order_by(CollectionTask.id.asc()).all()
        return {t.id: t.status for t in tasks}
    finally:
        sess.close()


def test_create_first_task_promoted_to_running(client: TestClient):
    """When no task is running, a new task is promoted to running (submitted)."""
    resp = client.post("/api/tasks", data=_create_task_form("alpha"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "submitted"
    statuses = _get_task_statuses()
    assert list(statuses.values()) == ["running"]


def test_create_second_task_while_running_is_queued_pending(client: TestClient):
    """PRD §8.2 场景 2.2: while A is running, B is created as pending (queued)."""
    # Task A → running
    client.post("/api/tasks", data=_create_task_form("alpha"))
    # Task B while A running → pending, queued
    resp = client.post("/api/tasks", data=_create_task_form("beta"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "queued"
    statuses = list(_get_task_statuses().values())
    # Exactly one running, one pending — never two running.
    assert statuses.count("running") == 1
    assert statuses.count("pending") == 1


def test_create_third_task_still_only_one_running(client: TestClient):
    """A running + B pending: creating C keeps a single running (C queued too)."""
    client.post("/api/tasks", data=_create_task_form("alpha"))
    client.post("/api/tasks", data=_create_task_form("beta"))
    resp = client.post("/api/tasks", data=_create_task_form("gamma"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    statuses = list(_get_task_statuses().values())
    assert statuses.count("running") == 1
    assert statuses.count("pending") == 2


# ---------------------------------------------------------------------------
# S6 — P3 UI behavior (T30-T36)
# ---------------------------------------------------------------------------

def test_base_includes_htmx_script(client: TestClient):
    """T30: base.html must load htmx.js (else all hx- attrs are inert)."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "htmx.org" in resp.text


def test_base_has_heartbeat_indicator(client: TestClient):
    """T35: base.html nav has the heartbeat indicator polling /api/heartbeat."""
    resp = client.get("/")
    assert "heartbeat-indicator" in resp.text
    assert "/api/heartbeat" in resp.text


def test_app_js_has_htmx_error_listeners(client: TestClient):
    """T36: app.js registers htmx:responseError + htmx:sendError → red Toast."""
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "htmx:responseError" in resp.text
    assert "htmx:sendError" in resp.text
    assert "系统异常，操作失败，请检查后台日志" in resp.text


# --- T31 status badge ------------------------------------------------------

def _make_task(client: TestClient, target_value="alpha", status="pending"):
    """Create a task via POST and force its DB status for badge tests."""
    resp = client.post("/api/tasks", data=_create_task_form(target_value))
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    request_id = resp.json()["request_id"]
    if status != "running":
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            t.status = status
            sess.commit()
        finally:
            sess.close()
    return task_id, request_id


def test_status_badge_need_human_blinks(client: TestClient):
    """T31: need_human → red blink badge + 人工文案."""
    task_id, _ = _make_task(client, status="need_human")
    resp = client.get(f"/api/tasks/{task_id}/status")
    assert resp.status_code == 200
    assert "blink" in resp.text
    assert "需人工处理验证码" in resp.text


def test_status_badge_completed_success(client: TestClient):
    """T31: completed → success badge."""
    task_id, _ = _make_task(client, status="completed")
    resp = client.get(f"/api/tasks/{task_id}/status")
    assert "success" in resp.text
    assert "已完成" in resp.text


def test_status_badge_night_sleep_transient(client: TestClient):
    """T31: running + progress message=night_sleep → dark badge + 07:00 文案."""
    import time
    from semilabs_hone.core.ipc import paths as ipc_paths

    task_id, request_id = _make_task(client, status="running")
    # Fresh heartbeat so the watchdog doesn't reap this running task mid-test.
    ipc_paths.write_heartbeat(now=time.time())
    # Write a progress file the badge endpoint correlates via task.request_id.
    ipc_paths.atomic_write_json(
        ipc_paths.progress_path(request_id),
        {"request_id": request_id, "message": "night_sleep",
         "data": {"wakeup": "08:00"}, "updated_at": 0},
    )
    resp = client.get(f"/api/tasks/{task_id}/status")
    assert "night-sleep" in resp.text
    assert "07:00" in resp.text


def test_task_detail_renders_badge(client: TestClient):
    """T31: detail page renders the pollable badge span."""
    task_id, _ = _make_task(client, status="running")
    resp = client.get(f"/tasks/{task_id}")
    assert "badge-" + task_id in resp.text
    assert "/api/tasks/" + task_id + "/status" in resp.text


# --- T32 create-task dialog & form migration --------------------------------

def test_new_task_page_renders_dialog(client: TestClient):
    """T32: /tasks/new renders a <dialog> + the new PRD form fields."""
    resp = client.get("/tasks/new")
    assert resp.status_code == 200
    assert "<dialog" in resp.text
    assert 'id="dlg-new"' in resp.text
    assert 'name="task_type"' in resp.text
    assert 'name="target_value"' in resp.text
    assert 'name="expected_count"' in resp.text


def test_create_task_keyword_search_ok(client: TestClient):
    """T32: keyword_search stores target_value + sets request_id."""
    resp = client.post("/api/tasks", data=_create_task_form("旅行"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "submitted"

    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask
    sess = get_session()
    try:
        t = sess.query(CollectionTask).filter(CollectionTask.id == body["task_id"]).first()
        assert t.task_type == "keyword_search"
        assert t.target_value == "旅行"
        assert t.expected_count == 10
        assert t.request_id == body["request_id"]
    finally:
        sess.close()


def test_create_task_count_clamped_to_200(client: TestClient):
    """T32: expected_count > 200 is clamped to 200 (PRD §8.2 场景 2.1)."""
    form = _create_task_form("clamp")
    form["expected_count"] = 999
    resp = client.post("/api/tasks", data=form)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask
    sess = get_session()
    try:
        t = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        assert t.expected_count == 200
    finally:
        sess.close()


def test_create_task_author_homepage_invalid_http_rejected(client: TestClient):
    """T32: author_homepage with non-http target → 400."""
    form = _create_task_form("not-a-url")
    form["task_type"] = "author_homepage"
    resp = client.post("/api/tasks", data=form)
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


# --- T33 optimistic lock & need_human buttons ------------------------------

def test_task_detail_need_human_buttons(client: TestClient):
    """T33: need_human detail renders 唤起浏览器 + 已处理，继续 + optimistic lock."""
    task_id, _ = _make_task(client, status="need_human")
    resp = client.get(f"/tasks/{task_id}")
    assert "唤起浏览器" in resp.text
    assert "已处理，继续" in resp.text
    assert "hx-disabled-elt" in resp.text


def test_task_detail_running_cancel_optimistic(client: TestClient):
    """T33: running detail renders cancel button with optimistic lock."""
    task_id, _ = _make_task(client, status="running")
    resp = client.get(f"/tasks/{task_id}")
    assert "取消任务" in resp.text
    assert "hx-disabled-elt" in resp.text


# --- T34 master-detail comments --------------------------------------------

def _make_item_with_comments(client: TestClient, n_comments=2):
    """Create a task + an item + n comments via repository upsert."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models import repository as repo

    platform = "xiaohongshu"
    # Create a task to satisfy item.task_id FK.
    resp = client.post("/api/tasks", data=_create_task_form("seed"))
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    sess = get_session()
    try:
        item = repo.upsert_item(
            sess, task_id=task_id, platform=platform, platform_id="pid-item-1",
            title="标题", content_text="正文", author_name="作者",
        )
        for i in range(n_comments):
            repo.upsert_comment(
                sess, item_id=item.id, platform_comment_id=f"cid-{i}",
                author_name=f"评论者{i}", content_text=f"评论内容{i}",
                like_count=i,
            )
        return item.id
    finally:
        sess.close()


def test_item_comments_with_data(client: TestClient):
    """T34: GET /api/items/<id>/comments returns a <tr> fragment with comments."""
    item_id = _make_item_with_comments(client, n_comments=2)
    resp = client.get(f"/api/items/{item_id}/comments")
    assert resp.status_code == 200
    assert resp.text.startswith("<tr")
    assert "评论者0" in resp.text
    assert "评论者1" in resp.text


def test_item_comments_empty_grey_text(client: TestClient):
    """T34: item with no comments → 置灰文案."""
    item_id = _make_item_with_comments(client, n_comments=0)
    resp = client.get(f"/api/items/{item_id}/comments")
    assert resp.status_code == 200
    assert "该笔记暂无评论数据" in resp.text


def test_posts_page_has_master_detail_toggle(client: TestClient):
    """T34: posts list wires row click → toggleComments + /api/items/ endpoint."""
    _make_item_with_comments(client, n_comments=1)
    resp = client.get("/posts")
    assert resp.status_code == 200
    assert "toggleComments" in resp.text
    assert "/api/items/" in resp.text


# --- T35 heartbeat indicator ------------------------------------------------

def test_heartbeat_fresh_green(client: TestClient, tmp_data_dir):
    """T35: fresh heartbeat → green + 引擎运行中."""
    import time
    from semilabs_hone.core.ipc import paths as ipc_paths
    ipc_paths.write_heartbeat(now=time.time())
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200
    assert "green" in resp.text
    assert "引擎运行中" in resp.text


def test_heartbeat_stale_red(client: TestClient, tmp_data_dir):
    """T35: stale heartbeat (>30s) → red + 离线文案."""
    import time
    from semilabs_hone.core.ipc import paths as ipc_paths
    ipc_paths.write_heartbeat(now=time.time() - 60)
    resp = client.get("/api/heartbeat")
    assert "red" in resp.text
    assert "离线" in resp.text


def test_heartbeat_absent_red(client: TestClient, tmp_data_dir):
    """T35: no heartbeat file → red + 离线文案."""
    # tmp_data_dir creates an empty ipc/progress/ — no heartbeat.json written.
    resp = client.get("/api/heartbeat")
    assert "red" in resp.text
    assert "离线" in resp.text


# ---------------------------------------------------------------------------
# S6b — P3.5 task console list page (T37-T39)
# ---------------------------------------------------------------------------

# --- T37 list page + empty state -------------------------------------------

def test_tasks_list_page_empty_state(client: TestClient):
    """T37: GET /tasks with no tasks → 空状态卡片 + 新建任务按钮."""
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert "暂无采集任务" in resp.text
    assert "新建任务" in resp.text


def test_tasks_list_page_renders_rows(client: TestClient):
    """T37: GET /tasks lists tasks with status-/actions- cells + tasks-tbody."""
    tid_a, _ = _make_task(client, target_value="alpha", status="running")
    tid_b, _ = _make_task(client, target_value="beta", status="pending")
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert 'id="tasks-tbody"' in resp.text
    assert "alpha" in resp.text and "beta" in resp.text
    assert f'id="status-{tid_a}"' in resp.text
    assert f'id="actions-{tid_b}"' in resp.text
    assert f'id="row-{tid_a}"' in resp.text


def test_list_page_includes_create_dialog(client: TestClient):
    """T39: the list page embeds the create-task dialog (afterbegin insert target)."""
    resp = client.get("/tasks")
    assert 'id="dlg-new"' in resp.text
    assert 'name="task_type"' in resp.text


# --- T38 row / actions fragments -------------------------------------------

def test_task_row_fragment(client: TestClient):
    """T38: GET /api/tasks/<id>/row returns a <tr> with row-/status-/actions- ids."""
    task_id, _ = _make_task(client, target_value="rowitem", status="running")
    resp = client.get(f"/api/tasks/{task_id}/row")
    assert resp.status_code == 200
    assert resp.text.lstrip().startswith("<tr")
    assert f'id="row-{task_id}"' in resp.text
    assert f'id="status-{task_id}"' in resp.text
    assert f'id="actions-{task_id}"' in resp.text


def test_task_actions_fragment_running(client: TestClient):
    """T38: running task → actions fragment has 取消 + optimistic lock."""
    task_id, _ = _make_task(client, status="running")
    resp = client.get(f"/api/tasks/{task_id}/actions")
    assert "取消" in resp.text
    assert "hx-disabled-elt" in resp.text


def test_task_actions_fragment_need_human(client: TestClient):
    """T38: need_human → 唤起浏览器 + 已处理，继续."""
    task_id, _ = _make_task(client, status="need_human")
    resp = client.get(f"/api/tasks/{task_id}/actions")
    assert "唤起浏览器" in resp.text
    assert "已处理，继续" in resp.text


def test_task_actions_fragment_completed(client: TestClient):
    """T38: completed → 导出 CSV."""
    task_id, _ = _make_task(client, status="completed")
    resp = client.get(f"/api/tasks/{task_id}/actions")
    assert "导出 CSV" in resp.text


def test_task_actions_fragment_pending_placeholder(client: TestClient):
    """T38: pending (no action) → 置灰占位, no action buttons."""
    task_id, _ = _make_task(client, status="pending")
    resp = client.get(f"/api/tasks/{task_id}/actions")
    assert "—" in resp.text
    assert "取消" not in resp.text
    assert "继续" not in resp.text


# --- T39 create→afterbegin wiring (regression on the dialog partial) -------

def test_new_task_page_still_renders_dialog_after_extract(client: TestClient):
    """T39 refactor: /tasks/new still renders the dialog + PRD form fields."""
    resp = client.get("/tasks/new")
    assert resp.status_code == 200
    assert 'id="dlg-new"' in resp.text
    assert 'name="task_type"' in resp.text
    assert 'name="target_value"' in resp.text
    assert 'name="expected_count"' in resp.text


def test_row_fragment_after_create_matches_list(client: TestClient):
    """T39: after creating a task, the /row fragment is insertable (afterbegin target)."""
    resp = client.post("/api/tasks", data=_create_task_form("freshrow"))
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    row = client.get(f"/api/tasks/{task_id}/row")
    assert row.status_code == 200
    assert row.text.lstrip().startswith("<tr")
    # The inserted row must carry the poll hooks so it self-refreshes in-list.
    assert f'/api/tasks/{task_id}/actions' in row.text

