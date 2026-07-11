"""PRD §8.2 — 任务下发与并发队列验收 (Task Creation & Queue).

BDD acceptance tests for scenarios 2.1 (输入合法性校验) and 2.2 (单节点并发排队).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/core/test_routes.py)
# ---------------------------------------------------------------------------

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


def _create_task_form(*, target_value="alpha", task_type="keyword_search",
                      expected_count=10) -> dict:
    """PRD §4.1.1 form (S6/T32): task_type/target_value/expected_count."""
    return {
        "account_id": 0,
        "platform": "xiaohongshu",
        "task_type": task_type,
        "target_value": target_value,
        "expected_count": expected_count,
        "sort": "general",
        "download_images": "false",
        "collect_comments": "false",
    }


def _task_statuses() -> dict:
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask
    sess = get_session()
    try:
        tasks = sess.query(CollectionTask).order_by(CollectionTask.id.asc()).all()
        return {t.id: t.status for t in tasks}
    finally:
        sess.close()


# ─── 场景 2.1：输入合法性与防呆极致校验 ────────────────────────────────

class TestScenario21InputValidation:
    """PRD §8.2 场景 2.1.

    Given 用户在定向 URL 模式下输入了不包含 http 的文本，或包含了恶意 SQL 注入字符.
    When  用户框失去焦点.
    Then  前端必须正则校验失败，输入框变红并禁用提交按钮.
    """

    def test_author_homepage_without_http_rejected_at_backend(self, client):
        """author_homepage target_value lacking http(s):// → 400 (backend safety net).

        PRD 2.1 Then: 校验失败，禁用提交. The frontend regex gate mirrors this
        backend enforcement (schemas.TaskCreate model_validator). A non-http URL
        for an author_homepage task is rejected with 400 + {ok:false}.
        """
        resp = client.post("/api/tasks", data=_create_task_form(
            task_type="author_homepage", target_value="xiaohongshu.com/user/123"))
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("ok") is False

    def test_sql_injection_target_value_stored_literal_not_executed(self, client):
        """A SQL-injection string as a keyword is stored literally (parameterized).

        PRD 2.1 Then (safety side): 恶意 SQL 注入字符 must not corrupt the DB.
        SQLAlchemy parameterizes the target_value; the string is stored verbatim
        and the DB is intact (no dropped tables, no OR-1=1 expansion).
        """
        poison = "'; DROP TABLE collection_tasks;--"
        resp = client.post("/api/tasks", data=_create_task_form(target_value=poison))
        assert resp.status_code == 200

        # The literal poison string is stored verbatim — not executed.
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(
                CollectionTask.target_value == poison).first()
            assert task is not None
            assert task.target_value == poison
        finally:
            sess.close()

        # The table still exists + is queryable (no DROP executed).
        statuses = _task_statuses()
        assert len(statuses) >= 1


# ─── 场景 2.2：单节点并发排队机制 ──────────────────────────────────────

class TestScenario22ConcurrencyQueue:
    """PRD §8.2 场景 2.2.

    Given 任务 A 正在 running.
    When  用户在 UI 上成功创建了任务 B 和任务 C.
    Then  任务 B 和 C 的状态必须为 pending (单节点单浏览器，同时仅 1 running).
    """

    def test_first_task_promoted_to_running(self, client):
        """When no task is running, the first created task becomes running."""
        resp = client.post("/api/tasks", data=_create_task_form(target_value="alpha"))
        assert resp.status_code == 200
        statuses = list(_task_statuses().values())
        assert statuses == ["running"]

    def test_second_and_third_tasks_queued_pending(self, client):
        """While A is running, creating B and C leaves them pending — exactly one running."""
        client.post("/api/tasks", data=_create_task_form(target_value="alpha"))
        client.post("/api/tasks", data=_create_task_form(target_value="beta"))
        client.post("/api/tasks", data=_create_task_form(target_value="gamma"))

        statuses = list(_task_statuses().values())
        # PRD 2.2 Then: B and C pending; exactly one running (A), never two.
        assert statuses.count("running") == 1
        assert statuses.count("pending") == 2
