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

#: Task states that occupy the single-task-at-a-time slot (PRD §8.2 场景 2.2).
ACTIVE_STATES = ("pending", "running")


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _ipc_client():
    from semilabs_hone.core.ipc.client import IPCClient
    from semilabs_hone.core.ipc.protocol import IPCRequest
    return IPCClient, IPCRequest


def _error(message: str, fix_hint: str, status_code: int) -> JSONResponse:
    """Uniform machine- and human-readable rejection (G8)."""
    return JSONResponse(
        {"ok": False, "error": message, "fix_hint": fix_hint}, status_code=status_code
    )


def _resolve_active_account_id(sess, platform: str) -> int | None:
    """Most-recently-logged-in active account for a platform (PRD §6.1 has no
    task→account FK; resume resolves the worker account from the platform)."""
    from semilabs_hone.core.models.account import Account

    acct = (
        sess.query(Account)
        .filter(Account.platform == platform, Account.status == "active")
        .order_by(Account.last_login_at.desc().nullslast(), Account.id.desc())
        .first()
    )
    return acct.id if acct else None


def _validate_new_task(sess, platform: str, account_id: int, targets: list[str]):
    """G8 pre-flight checks; return a JSONResponse on the first problem, else None.

    Failing deep inside the worker leaves a zombie task and a confused user —
    validate platform / keywords / account (exists, same platform, logged in)
    and the single-task slot up front, answering 4xx with a fix_hint.
    """
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.core.models.task import CollectionTask
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms

    known = list_platforms()
    if platform not in known:
        return _error(
            f"未知平台 '{platform}'",
            f"可用平台: {', '.join(known) or '（无）'}；新站点需先录制生成 platform.yaml",
            400,
        )

    if not targets:
        return _error("关键词/目标不能为空", "至少填一个关键词或主页链接，多个用逗号或换行分隔", 400)

    account = sess.query(Account).filter(Account.id == account_id).first()
    if account is None:
        return _error(f"账号 {account_id} 不存在", "先到账号页添加并登录一个账号", 400)
    if account.platform != platform:
        return _error(
            f"账号 #{account_id} 属于 {account.platform}，与所选平台 {platform} 不符",
            "选择同平台的账号，或为该平台新建账号",
            400,
        )
    if account.status != "active":
        return _error(
            f"账号 #{account_id} 未登录（状态 {account.status}）",
            "先在账号页点『登录』完成扫码，再来建任务",
            409,
        )
    return None


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
            label = task.error_msg or "error"
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
            f'<button onclick="exportCsv(\'{tid}\', this)" class="button">导出 CSV</button>'
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


@router.get("/api/tasks/{task_id}/progress")
async def api_task_progress(task_id: str) -> JSONResponse:
    """GET /api/tasks/{id}/progress — latest progress snapshot (PRD §5.4).

    Resolves the task's `request_id` → `progress/<rid>.json` and returns the
    IPCProgress payload (message/data). Polled by task_detail.html as a WS
    fallback; 404 when the task or progress file is absent (frontend treats
    both as "waiting").
    """
    from semilabs_hone.core.ipc import paths as ipc_paths
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        rid = task.request_id if task is not None else None
    finally:
        sess.close()

    if not rid:
        return JSONResponse({"ok": False, "error": "no request_id"}, status_code=404)
    prog = ipc_paths.read_json_if_exists(ipc_paths.progress_path(rid))
    if prog is None:
        return JSONResponse({"ok": False, "error": "no progress yet"}, status_code=404)
    return JSONResponse({"ok": True, **prog})

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
    is untouched (向后兼容). Single-running lock (PRD §8.2 场景 2.2) enforced by
    the G8 pre-flight checks (409 + fix_hint), not silent queueing.

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
        # G8: fail fast with a fix_hint instead of deep inside the worker.
        rejection = _validate_new_task(sess, platform, account_id, targets)
        if rejection is not None:
            return rejection

        # PRD §2.2 场景1: multiple tasks queue (pending), one runs at a time.
        already_running = sess.query(CollectionTask).filter(
            CollectionTask.status.in_(ACTIVE_STATES)
        ).first() is not None

        task = CollectionTask(
            platform=platform,
            status="pending" if already_running else "running",
            task_type=task_type,
            target_value=stored_target,
            expected_count=expected_count,
        )
        sess.add(task)
        sess.flush()
        task_id = task.id
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

    # L13: ensure a worker is alive to consume the request we just wrote. No-op
    # when auto-spawn is off (tests) — app.state has no spawner then.
    _ensure_worker(request, account_id)

    return JSONResponse({
        "ok": True,
        "request_id": request_id,
        "task_id": task_id,
        "status": "queued" if already_running else "submitted",
    })


def _ensure_worker(request: Request, account_id: int | None) -> None:
    """Best-effort spawn of the collection worker for this account (L13).

    Attached to app.state only when config.WORKER_AUTOSPAWN is on; absent in
    tests (bare create_app / WORKER_AUTOSPAWN=0) so this is a silent no-op there.
    """
    spawner = getattr(request.app.state, "worker_spawner", None)
    if spawner is None or account_id is None:
        return
    try:
        spawner(account_id)
    except Exception:
        pass


@router.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/cancel — cancel running task.

    Sets DB status to ``cancelled`` and writes the legacy cancel sentinel keyed
    by the task's ``request_id`` (F3: the worker polls ``cancel_sentinel(rid)``,
    not ``task-{task_id}``). The sentinel must exist before the worker checks it
    on its next step boundary.
    """
    IPCClient, _ = _ipc_client()
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    rid: str | None = None
    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if task:
            task.status = "cancelled"
            rid = task.request_id  # capture before commit (L12 DetachedInstanceError)
            sess.commit()
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()

    # Write the cancel sentinel keyed by request_id so the live worker sees it
    # (server._is_cancelled checks cancel_sentinel(request_id), F3).
    if rid:
        client = IPCClient()
        client.cancel(rid)
    return JSONResponse({"ok": True})


@router.post("/api/tasks/{task_id}/resume")
async def api_resume_task(request: Request, task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/resume — resume a suspended/failed task.

    Two paths (L01):
    - ``need_human`` + live ``request_id``: the worker is still alive, suspended
      in ``_await_resume`` polling ``control/``. Write ``control/ctrl_<rid>.json
      {action:resume}`` (PRD §4.4.3 step4) — the worker reads-after-burns it and
      re-runs the interrupted action. No new IPC request (the page state is live).
    - ``paused`` / ``error`` / ``failed``: the worker likely exited (or was reaped
      by the heartbeat watchdog). Re-submit a fresh ``scrape_task`` request that
      the (re-spawned) worker picks up from the persisted ``last_note_index``.

    All task attributes are captured into locals BEFORE ``sess.close()`` to avoid
    the DetachedInstanceError (L12): commit triggers expire_on_commit, so any ORM
    attribute access after close raises.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    # Captured-locals holder (populated only inside the session scope below).
    rid: str | None = None
    need_human_resume = False
    new_req_fields: dict = {}

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

        need_human_resume = (
            task.status == "need_human" and bool(task.request_id)
        )

        # Flip to running + clear the error marker (both paths).
        task.status = "running"
        task.error_msg = None

        # Resolve the worker account from the platform (PRD §6.1 has no
        # task→account FK) BEFORE close (L12).
        resume_account_id = _resolve_active_account_id(sess, task.platform)
        sess.commit()

        # Capture everything we need BEFORE close (L12). After commit, attributes
        # are expired; close() detaches the instance → any attr access raises.
        if need_human_resume:
            rid = task.request_id
        else:
            new_req_fields = {
                "account_id": resume_account_id,
                "platform": task.platform,
                "task_type": task.task_type,
                "target_value": task.target_value,
                "expected_count": task.expected_count,
            }
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()

    # --- Path A: need_human → control file (PRD §4.4.3 step4) ---
    if need_human_resume and rid:
        from semilabs_hone.core.ipc.paths import atomic_write_json, control_path
        atomic_write_json(control_path(rid), {"action": "resume"})
        return JSONResponse({
            "ok": True,
            "request_id": rid,
            "task_id": task_id,
            "status": "resumed",
        })

    # --- Path B: re-submit a fresh scrape_task request (worker exited/reaped) ---
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]
    account_id = new_req_fields.get("account_id")

    # Persist the fresh request_id so badge↔progress correlation and a later
    # cancel/resume address the live request (L01).
    sess3 = get_session()
    try:
        t = sess3.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if t is not None:
            t.request_id = request_id
            sess3.commit()
    except Exception:
        sess3.rollback()
    finally:
        sess3.close()

    task_type = new_req_fields.get("task_type") or "keyword_search"
    target_value = new_req_fields.get("target_value") or ""

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="scrape_task",
        account_id=account_id,
        payload={
            "task_id": task_id,
            "platform": new_req_fields.get("platform", "xiaohongshu"),
            "task_type": task_type,
            "target_value": target_value,
            "keywords": [target_value] if task_type == "keyword_search" and target_value else [],
            "sort": "general",
            "account_id": account_id,
            "request_id": request_id,
            "max_posts_per_keyword": new_req_fields.get("expected_count", 20),
            "download_images": True,
            "collect_comments": True,
            "resume": True,
        },
    )

    client = IPCClient()
    client.submit(req)
    _ensure_worker(request, account_id)

    return JSONResponse({
        "ok": True,
        "request_id": request_id,
        "task_id": task_id,
        "status": "submitted",
    })
