"""Task management routes — create/list/cancel/resume.

Design: docs/skim_design.md §13.1.
- Platform dropdown from registry.list_platforms()
- Only 1 running task at a time (check DB)
- POST /api/tasks → IPC submit → {request_id, status}

[契约变更 2026-07-10] S3: model renamed ScrapeTask→CollectionTask; task PK is now
a UUID str. This route still accepts the legacy form (account_id/keywords/sort/
max_posts/download_images/collect_comments) and derives the PRD §6.1 columns
(task_type/target_value/expected_count) from it, so the create flow stays green
while the full dialog/form migration is deferred to S6/T32. task_id path/IPC now
str (UUID).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _ipc_client():
    from semilabs_hone.core.ipc.client import IPCClient
    from semilabs_hone.core.ipc.protocol import IPCRequest
    return IPCClient, IPCRequest


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/tasks", response_class=HTMLResponse)
async def page_tasks_list(request: Request) -> HTMLResponse:
    """GET /tasks — task console list page (PRD §5.2).

    Lists all tasks (newest first) with a polled status badge cell and a polled
    actions cell. Empty state card when there are no tasks. Row click → detail.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        tasks = (
            sess.query(CollectionTask)
            .order_by(CollectionTask.created_at.desc())
            .all()
        )
        rows = [_row_context(t) for t in tasks]
    except Exception:
        rows = []
    finally:
        sess.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "tasks_list.html",
        {"rows": rows, "has_tasks": bool(rows)},
    )


@router.get("/tasks/new", response_class=HTMLResponse)
async def page_new_task(request: Request) -> HTMLResponse:
    """GET /tasks/new — create task page with platform/keyword form."""
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms

    platforms = list_platforms()

    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        accounts = sess.query(Account).order_by(Account.id.desc()).all()
    except Exception:
        accounts = []
    finally:
        sess.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "task_new.html",
        {"platforms": platforms, "accounts": accounts},
    )


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def page_task_detail(request: Request, task_id: str) -> HTMLResponse:
    """GET /tasks/{id} — task detail page with progress."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
    except Exception:
        task = None
    finally:
        sess.close()

    if task is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Task not found")

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "task_detail.html",
        {"task": task, "badge_html": _badge_html(task)},
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

# Status -> (badge class, label) for the task status badge (PRD §5.2.2).
# `night_sleep` / `resting` are IPC transients (not DB status); when the task is
# `running` we read the latest progress file (keyed by task.request_id) to surface
# the transient stage. Falls back to a plain "running" badge when no progress
# file is correlated (e.g. worker not yet wired to push progress — S4/S5 gap).
_BADGE_MAP = {
    "pending": ("muted", "排队中..."),
    "completed": ("success", "已完成"),
    "need_human": ("error blink", "需人工处理验证码"),
    "paused": ("warning", "已暂停"),
    "error": ("error", "error"),
    "failed": ("error", "error"),
}


def _running_transient_badge(task) -> tuple[str, str]:
    """Read the latest progress file for this task to pick a transient badge.

    Returns (badge_class, label). Defaults to ("active", "运行中") when no
    progress file is found or the message is unrecognized.
    """
    from semilabs_hone.core.ipc import paths as ipc_paths

    rid = task.request_id
    if not rid:
        return ("active", "运行中")
    try:
        prog = ipc_paths.read_json_if_exists(ipc_paths.progress_path(rid))
    except Exception:
        prog = None
    if not prog:
        return ("active", "运行中")
    msg = (prog.get("message") or "").lower()
    if msg == "night_sleep":
        return ("night-sleep", "夜间安全休眠中 (07:00 唤醒)")
    if msg == "resting":
        return ("active", "休息防封中")
    return ("active", "运行中")


def _badge_html(task) -> str:
    """Render a pollable <span class="badge ...">label</span> fragment for HTMX.

    The span carries its own hx-get/hx-trigger/hx-swap so that after an
    outerHTML swap the polling continues (htmx re-processes the new node).
    """
    status = task.status
    if status == "running":
        cls, label = _running_transient_badge(task)
    elif status in _BADGE_MAP:
        cls, label = _BADGE_MAP[status]
        if status in ("error", "failed"):
            label = task.error_msg or task.error_message or "error"
    else:
        cls, label = ("muted", status)
    tid = task.id
    return (
        f'<span id="badge-{tid}" class="badge {cls}" '
        f'hx-get="/api/tasks/{tid}/status" hx-trigger="every 5s" hx-swap="outerHTML">'
        f'{label}</span>'
    )


def _actions_html(task) -> str:
    """Render the action-buttons fragment for a task (PRD §5.2.3).

    Single source for both the task-detail page and the list-row `actions-<id>`
    cell. Buttons use hx-disabled-elt (optimistic lock during the in-flight
    request) + onclick lockBtn for immediate aria-busy; the list cell polls
    GET /api/tasks/{id}/actions every 5s so it refreshes to the new status's
    buttons once the backend state changes.
    """
    tid = task.id
    parts: list[str] = []
    if task.status == "running":
        parts.append(
            f'<button hx-post="/api/tasks/{tid}/cancel" hx-swap="none" '
            f'hx-disabled-elt="this" class="secondary outline" '
            f'onclick="lockBtn(this)">取消</button>'
        )
    if task.status == "need_human":
        parts.append(
            f'<a href="/tasks/{tid}" class="button primary" role="button" '
            f'title="请切换到 worker Chrome 完成扫码 / 验证">唤起浏览器</a>'
        )
        parts.append(
            f'<button hx-post="/api/tasks/{tid}/resume" hx-swap="none" '
            f'hx-disabled-elt="this" class="primary" '
            f'onclick="lockBtn(this)">已处理，继续</button>'
        )
    if task.status in ("failed", "error", "paused"):
        parts.append(
            f'<button hx-post="/api/tasks/{tid}/resume" hx-swap="none" '
            f'hx-disabled-elt="this" class="primary" '
            f'onclick="lockBtn(this)">继续</button>'
        )
    if task.status == "completed":
        parts.append(
            f'<a href="/api/export?task_id={tid}&format=ai" class="button">导出 CSV</a>'
        )
    return " ".join(parts) if parts else '<span style="color:var(--pico-muted-color)">—</span>'


def _row_context(task) -> dict:
    """Context for the _task_row.html partial (shared by list render & /row endpoint)."""
    return {"task": task, "badge": _badge_html(task), "actions": _actions_html(task)}


@router.get("/api/tasks/{task_id}/status")
async def api_task_status(task_id: str) -> HTMLResponse:
    """GET /api/tasks/{id}/status — status badge HTML fragment (PRD §5.2.2).

    Polled by HTMX (hx-trigger=every 5s) to refresh the badge without a full
    page reload. Returns a self-contained <span class="badge">...</span>.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if task is None:
            return HTMLResponse('<span class="badge error">未找到</span>', status_code=404)
        return HTMLResponse(_badge_html(task))
    finally:
        sess.close()


@router.get("/api/tasks/{task_id}/row")
async def api_task_row(task_id: str) -> HTMLResponse:
    """GET /api/tasks/{id}/row — full <tr> fragment for the list (PRD §5.3.2).

    Used for afterbegin-insert on create (htmx.ajax swap=afterbegin into
    #tasks-tbody). Renders the shared _task_row.html partial so the markup is
    identical to the server-rendered list rows.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if task is None:
            return HTMLResponse("<!-- task not found -->", status_code=404)
    finally:
        sess.close()

    t = _templates()
    assert t is not None, "Templates not initialized"
    html = t.env.get_template("_task_row.html").render(**_row_context(task))
    return HTMLResponse(html)


@router.get("/api/tasks/{task_id}/actions")
async def api_task_actions(task_id: str) -> HTMLResponse:
    """GET /api/tasks/{id}/actions — action-buttons fragment (PRD §5.2.3).

    Polled by the list-row `actions-<id>` cell every 5s; refreshes the buttons
    to match the current status after an optimistic-lock POST lands.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if task is None:
            return HTMLResponse("<!-- task not found -->", status_code=404)
        return HTMLResponse(_actions_html(task))
    finally:
        sess.close()

@router.post("/api/tasks")
async def api_create_task(
    request: Request,
    account_id: int = Form(default=0),
    platform: str = Form(default="xiaohongshu"),
    task_type: str = Form(default="keyword_search"),
    target_value: str = Form(default=""),
    expected_count: int = Form(default=20),
    sort: str = Form(default="general"),
    download_images: bool = Form(default=True),
    collect_comments: bool = Form(default=True),
) -> JSONResponse:
    """POST /api/tasks — create scrape task and enqueue via IPC.

    PRD §4.1.1 form (S6/T32 migration): task_type / target_value / expected_count.
    - keyword_search: target_value = comma/newline-separated keywords (≤10).
    - author_homepage: target_value = newline-separated http URL(s) (≤10).
    expected_count clamped to [1, 200] (PRD §8.2 场景 2.1).

    The legacy TaskKeyword/Keyword link chain is dropped (contract §2 cleanup);
    the IPC payload still derives `keywords` from target_value so the S4 engine
    is untouched (向后兼容). Single-running lock (PRD §8.2 场景 2.2) unchanged.

    Returns {request_id, status, task_id}.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    task_type = task_type if task_type in ("keyword_search", "author_homepage") else "keyword_search"
    # Split target_value on comma/newline, strip empties, cap at 10.
    flat: list[str] = []
    for line in target_value.replace("\r", "\n").split("\n"):
        for piece in line.split(","):
            s = piece.strip()
            if s:
                flat.append(s)
    targets = flat[:10]

    # author_homepage: every target must be http-prefixed (PRD §8.2 场景 2.1).
    if task_type == "author_homepage":
        bad = [t for t in targets if not t.lower().startswith("http")]
        if bad:
            return JSONResponse(
                {"ok": False, "error": "target_value 必须以 http 开头", "invalid": bad},
                status_code=400,
            )

    # Clamp expected_count to [1, 200].
    expected_count = max(1, min(200, expected_count))

    # PRD §6.1 target_value is a single String(255) — store the first target.
    stored_target = targets[0] if targets else ""

    sess = get_session()
    try:
        already_running = sess.query(CollectionTask).filter(
            CollectionTask.status == "running"
        ).first() is not None

        task = CollectionTask(
            account_id=account_id,
            platform=platform,
            status="pending",
            max_posts_per_keyword=expected_count,
            sort_type=sort,
            download_images=download_images,
            collect_comments=collect_comments,
            task_type=task_type,
            target_value=stored_target,
            expected_count=expected_count,
        )
        sess.add(task)
        sess.flush()
        task_id = task.id

        # Promote to running only when the single-running slot is free.
        if not already_running:
            task.status = "running"

        sess.commit()
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()

    # Submit IPC request regardless (worker picks up in mtime order; queued
    # tasks wait in requests/ until the current one finishes).
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    # Persist request_id on the task so the status badge can correlate the
    # progress file (progress/<rid>.json) and future resume→control wiring
    # (control/ctrl_<rid>.json, PRD §4.4.3) can target the live request.
    sess2 = get_session()
    try:
        t = sess2.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if t is not None:
            t.request_id = request_id
            sess2.commit()
    except Exception:
        sess2.rollback()
    finally:
        sess2.close()

    # Derive keywords for the engine (向后兼容 — S4 engine reads `keywords`).
    keywords_for_engine = targets if task_type == "keyword_search" else []

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="scrape_task",
        account_id=account_id,
        payload={
            "task_id": task_id,
            "platform": platform,
            "task_type": task_type,
            "target_value": stored_target,
            "keywords": keywords_for_engine,
            "target_urls": targets if task_type == "author_homepage" else [],
            "sort": sort,
            "max_posts_per_keyword": expected_count,
            "download_images": download_images,
            "collect_comments": collect_comments,
            "account_id": account_id,
            "request_id": request_id,
        },
    )

    client = IPCClient()
    client.submit(req)

    return JSONResponse({
        "ok": True,
        "request_id": request_id,
        "task_id": task_id,
        "status": "queued" if already_running else "submitted",
    })


@router.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/cancel — cancel running task."""
    IPCClient, _ = _ipc_client()
    # Cancel is done via IPC client cancel method
    # We also update DB status
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if task:
            task.status = "cancelled"
            sess.commit()

        # Cancel IPC (we need request_id; for simplicity, cancel by task_id)
        client = IPCClient()
        client.cancel(f"task-{task_id}")
        return JSONResponse({"ok": True})
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()


@router.post("/api/tasks/{task_id}/resume")
async def api_resume_task(task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/resume — resume failed task from checkpoint."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if not task:
            return JSONResponse({"ok": False, "error": "Task not found"}, status_code=404)

        # Single-running lock: only reject if ANOTHER task is already running
        # (a pending task does not block resuming this one — PRD §8.2 场景 2.2).
        other_running = sess.query(CollectionTask).filter(
            CollectionTask.id != task_id,
            CollectionTask.status == "running",
        ).first()
        if other_running:
            return JSONResponse(
                {"ok": False, "error": "Another task is already running"},
                status_code=409,
            )

        task.status = "running"
        task.error_message = None
        task.error_category = None
        sess.commit()
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()

    # Submit IPC resume request
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="scrape_task",
        account_id=task.account_id,
        payload={
            "task_id": task_id,
            "platform": task.platform,
            "account_id": task.account_id,
            "request_id": request_id,
            "max_posts_per_keyword": task.max_posts_per_keyword,
            "download_images": task.download_images,
            "collect_comments": task.collect_comments,
            "resume": True,
        },
    )

    client = IPCClient()
    client.submit(req)

    return JSONResponse({
        "ok": True,
        "request_id": request_id,
        "task_id": task_id,
        "status": "submitted",
    })
