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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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

@router.get("/tasks")
async def redirect_tasks_to_dashboard() -> RedirectResponse:
    """GET /tasks -> / — 任务大厅已合并到首页 (ui_design_spec_v2 §6).
    旧 tasks_list.html 路由保留为 /tasks-original, 块 8 收尾时统一删除.
    """
    return RedirectResponse("/", status_code=302)


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

# Status -> (Tailwind class, icon, label) for the task status badge (ui_design_spec_v2 §7.1).
# `night_sleep` / `resting` are IPC transients (not DB status); when the task is
# `running` we read the latest progress file (keyed by task.request_id) to surface
# the transient stage. Falls back to a plain "running" badge when no progress
# file is correlated (e.g. worker not yet wired to push progress — S4/S5 gap).
_BADGE_MAP = {
    "pending":    ("bg-yellow-500/15 text-yellow-400", "🟡", "排队中"),
    "completed":  ("bg-green-500/15 text-green-400",   "✅", "已完成"),
    "need_human": ("bg-red-500/15 text-red-400",       "⚠️", "需人工处理"),
    "paused":     ("bg-gray-500/15 text-gray-400",     "⏸️", "已暂停"),
    "error":      ("bg-purple-500/15 text-purple-400", "⛔", "异常中止"),
    "failed":     ("bg-purple-500/15 text-purple-400", "⛔", "异常中止"),
    "cancelled":  ("bg-gray-500/15 text-gray-400",     "⛔", "已取消"),
}


def _running_transient_badge(task) -> tuple[str, str, str]:
    """Read the latest progress file for this task to pick a transient badge.

    Returns (cls, icon, label). Defaults to running badge when no progress
    file is found or the message is unrecognized.
    """
    from semilabs_hone.core.ipc import paths as ipc_paths

    rid = task.request_id
    if not rid:
        return ("bg-green-500/15 text-green-400", "🟢", "运行中")
    try:
        prog = ipc_paths.read_json_if_exists(ipc_paths.progress_path(rid))
    except Exception:
        prog = None
    if not prog:
        return ("bg-green-500/15 text-green-400", "🟢", "运行中")
    msg = (prog.get("message") or "").lower()
    if msg == "night_sleep":
        return ("bg-gray-500/15 text-gray-400", "🌙", "夜间休眠")
    if msg == "resting":
        return ("bg-green-500/15 text-green-400", "🟢", "休息防封")
    return ("bg-green-500/15 text-green-400", "🟢", "运行中")


def _badge_html(task) -> str:
    """Render a pollable <span class="...">label</span> fragment for HTMX (Tailwind 暗色).

    The span carries its own hx-get/hx-trigger/hx-swap so that after an
    outerHTML swap the polling continues (htmx re-processes the new node).
    """
    status = task.status
    if status == "running":
        cls, icon, label = _running_transient_badge(task)
    elif status in _BADGE_MAP:
        cls, icon, label = _BADGE_MAP[status]
        if status in ("error", "failed"):
            label = task.error_msg or task.error_message or "异常中止"
    else:
        cls, icon, label = ("bg-yellow-500/15 text-yellow-400", "🟡", status)
    tid = task.id
    pulse = "animate-pulse" if status == "need_human" else ""
    return (
        f'<span id="badge-{tid}" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium {cls} {pulse}" '
        f'hx-get="/api/tasks/{tid}/status" hx-trigger="every 5s" hx-swap="outerHTML">'
        f'<span>{icon}</span><span>{label}</span></span>'
    )


def _actions_html(task) -> str:
    """Render the action-buttons fragment for a task (Tailwind 暗色, ui_design_spec_v2 §7.3).

    块1: 仅渲染已存在接口的分支 (paused→恢复, completed→查看+导出, error→重试).
    running 的暂停、need_human 的唤起/删除在后续块加.
    """
    tid = task.id
    parts: list[str] = []
    # 块3: running → 暂停
    if task.status == "running":
        parts.append(
            f'<button class="text-yellow-400 hover:text-yellow-300" title="暂停" '
            f'hx-post="/api/tasks/{tid}/pause" hx-swap="none">'
            f'<svg width="16" height="16" fill="currentColor">'
            f'<rect x="4" y="3" width="3" height="10"/><rect x="11" y="3" width="3" height="10"/>'
            f'</svg></button>'
        )
    # 块5 加: need_human → 唤起浏览器 + 已处理继续
    if task.status in ("failed", "error", "paused"):
        # 块1: paused/error/failed → 恢复 (复用 /resume)
        parts.append(
            f'<button class="text-green-400 hover:text-green-300" title="恢复" '
            f'hx-post="/api/tasks/{tid}/resume" hx-swap="none">'
            f'<svg width="16" height="16" fill="currentColor"><path d="M5 3l9 5-9 5V3z"/></svg></button>'
        )
    if task.status == "completed":
        # 块1: completed → 查看 (跳 /posts?task_id=X) + 导出 CSV
        parts.append(
            f'<a href="/posts?task_id={tid}" class="text-gray-400 hover:text-blue-400" title="查看">'
            f'<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">'
            f'<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'
            f'</svg></a>'
        )
        parts.append(
            f'<button onclick="exportCsv(\'{tid}\', this)" class="text-gray-400 hover:text-blue-400" title="导出">'
            f'<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">'
            f'<path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/>'
            f'</svg></button>'
        )
    # 块4 加: error → 删除
    return " ".join(parts) if parts else '<span class="text-gray-500 text-xs">—</span>'


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

        # Flip to running + clear the error markers (both paths).
        task.status = "running"
        task.error_message = None
        task.error_category = None
        sess.commit()

        # Capture everything we need BEFORE close (L12). After commit, attributes
        # are expired; close() detaches the instance → any attr access raises.
        if need_human_resume:
            rid = task.request_id
        else:
            new_req_fields = {
                "account_id": task.account_id,
                "platform": task.platform,
                "max_posts_per_keyword": task.max_posts_per_keyword,
                "download_images": task.download_images,
                "collect_comments": task.collect_comments,
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

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="scrape_task",
        account_id=account_id,
        payload={
            "task_id": task_id,
            "platform": new_req_fields.get("platform", "xiaohongshu"),
            "account_id": account_id,
            "request_id": request_id,
            "max_posts_per_keyword": new_req_fields.get("max_posts_per_keyword", 20),
            "download_images": new_req_fields.get("download_images", True),
            "collect_comments": new_req_fields.get("collect_comments", True),
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


# ---------------------------------------------------------------------------
# Block 3: Pause Task
# ---------------------------------------------------------------------------

@router.post("/api/tasks/{task_id}/pause")
async def api_pause_task(task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/pause — pause a running task.

    Writes IPC control file with {action: pause} to signal worker to stop.
    Updates task status to 'paused' in DB.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if not task:
            return JSONResponse({"ok": False, "error": "Task not found"}, status_code=404)

        if task.status != "running":
            return JSONResponse({"ok": False, "error": "Task is not running"}, status_code=409)

        task.status = "paused"
        sess.commit()

        # Write IPC control file to signal worker
        if task.request_id:
            from semilabs_hone.core.ipc.paths import atomic_write_json, control_path
            atomic_write_json(control_path(task.request_id), {"action": "pause"})

        return JSONResponse({
            "ok": True,
            "task_id": task_id,
            "status": "paused",
        })
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()
