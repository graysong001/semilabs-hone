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

@router.get("/tasks")
async def redirect_tasks_to_dashboard() -> RedirectResponse:
    """GET /tasks -> / — 任务大厅已合并到首页 (ui_design_spec_v2 §6)。

    独立的列表页/新建页/详情页已随 v2 任务大厅下线：建单走大厅弹窗，
    状态/操作走 /api/tasks/{id}/status|actions|row 片段轮询。
    """
    return RedirectResponse("/", status_code=302)


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
            label = task.error_msg or "异常中止"
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

    状态分支：
    - running → 暂停 (v2 块3) + 取消 (main P0d F3 cancel 修复保留)
    - need_human → 唤起浏览器 + 已处理继续 (v2 块5, openRiskModal)
    - paused/error/failed → 恢复
    - completed → 查看 + 导出 + 删除 (v2 块4)
    - error/failed → 删除 (v2 块4)
    Buttons use hx-disabled-elt (optimistic lock during the in-flight request);
    the list cell polls GET /api/tasks/{id}/actions every 5s so it refreshes to
    the new status's buttons once the backend state changes.
    """
    tid = task.id
    parts: list[str] = []
    # 块3: running → 暂停 + 取消
    if task.status == "running":
        parts.append(
            f'<button class="text-yellow-400 hover:text-yellow-300" title="暂停" '
            f'hx-post="/api/tasks/{tid}/pause" hx-target="#actions-{tid}" hx-swap="innerHTML" '
            f'hx-disabled-elt="this" hx-on::after-request="if(event.detail.successful) htmx.ajax(\'GET\', \'/api/tasks/{tid}/actions\', \'#actions-{tid}\')">'
            f'<svg width="16" height="16" fill="currentColor">'
            f'<rect x="4" y="3" width="3" height="10"/><rect x="11" y="3" width="3" height="10"/>'
            f'</svg></button>'
        )
        parts.append(
            f'<button class="text-red-400 hover:text-red-300" title="取消" '
            f'hx-post="/api/tasks/{tid}/cancel" hx-target="#actions-{tid}" hx-swap="innerHTML" '
            f'hx-disabled-elt="this" hx-on::after-request="if(event.detail.successful) htmx.ajax(\'GET\', \'/api/tasks/{tid}/actions\', \'#actions-{tid}\')">'
            f'<svg width="16" height="16" fill="currentColor"><rect x="4" y="4" width="8" height="8"/></svg></button>'
        )
    # 块5: need_human → 唤起浏览器 + 已处理继续
    if task.status == "need_human":
        target = (task.target_value or '').replace("'", "\\'")[:30]
        parts.append(
            f'<button class="px-3 py-1 bg-red-500/15 text-red-400 border border-red-500/30 rounded text-xs font-medium hover:bg-red-500/25" '
            f'onclick="openRiskModal(\'{tid}\', \'{target}\')">'
            f'🖥️ 唤起浏览器</button>'
        )
        parts.append(
            f'<button class="px-3 py-1 bg-green-500/15 text-green-400 border border-green-500/30 rounded text-xs font-medium hover:bg-green-500/25" '
            f'onclick="openRiskModal(\'{tid}\', \'{target}\')">'
            f'✅ 已处理</button>'
        )
    if task.status in ("failed", "error", "paused"):
        # 块1: paused/error/failed → 恢复 (复用 /resume)
        parts.append(
            f'<button class="text-green-400 hover:text-green-300" title="恢复" '
            f'hx-post="/api/tasks/{tid}/resume" hx-target="#actions-{tid}" hx-swap="innerHTML" '
            f'hx-disabled-elt="this" hx-on::after-request="if(event.detail.successful) htmx.ajax(\'GET\', \'/api/tasks/{tid}/actions\', \'#actions-{tid}\')">'
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
        # 块4: completed → 删除
        parts.append(
            f'<button class="text-red-400 hover:text-red-300" title="删除" '
            f'hx-delete="/api/tasks/{tid}" hx-target="#task-row-{tid}" hx-swap="outerHTML" hx-confirm="确定要删除这个任务吗？">'
            f'<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">'
            f'<path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/>'
            f'</svg></button>'
        )
    if task.status in ("error", "failed"):
        # 块4: error/failed → 删除
        parts.append(
            f'<button class="text-red-400 hover:text-red-300" title="删除" '
            f'hx-delete="/api/tasks/{tid}" hx-target="#task-row-{tid}" hx-swap="outerHTML" hx-confirm="确定要删除这个任务吗？">'
            f'<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">'
            f'<path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/>'
            f'</svg></button>'
        )
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
            return HTMLResponse(
                '<span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs '
                'bg-red-500/15 text-red-400">未找到</span>',
                status_code=404,
            )
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
    html = t.env.get_template("partials/_task_row.html").render(**_row_context(task))
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
    IPCProgress payload (message/data). Consumed by the dashboard as a WS
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
        # v2 任务大厅弹窗不选账号 (account_id=0)：按平台解析最近登录的 active
        # 账号 (PRD §6.1 任务不落账号 FK, worker 账号由 IPC payload 携带)。
        # 未知平台跳过解析，交给 _validate_new_task 报 400；已知平台无 active
        # 账号 → 409。显式 account_id 仍走原 G8 逐条校验。
        if not account_id:
            from semilabs_hone.modules.collection.scrapers.registry import list_platforms
            if platform in list_platforms():
                resolved = _resolve_active_account_id(sess, platform)
                if resolved is None:
                    return _error(
                        f"平台 {platform} 没有已登录账号",
                        "先到账号页登录一个该平台账号，再来建任务",
                        409,
                    )
                account_id = resolved

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


# ---------------------------------------------------------------------------
# Pause Task (v2 块3)
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


# ---------------------------------------------------------------------------
# Delete Task (v2 块4)
# ---------------------------------------------------------------------------

@router.delete("/api/tasks/{task_id}")
async def api_delete_task(task_id: str) -> JSONResponse:
    """DELETE /api/tasks/{id} — delete a task and its associated data.

    Only allows deleting completed/error/failed tasks. Running/pending tasks
    cannot be deleted. Cascades to posts and comments via DB foreign key.
    Also cleans up IPC control files if they exist.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if not task:
            return JSONResponse({"ok": False, "error": "Task not found"}, status_code=404)

        if task.status in ("running", "pending"):
            return JSONResponse(
                {"ok": False, "error": "Cannot delete running or pending tasks"},
                status_code=409
            )

        # Clean up IPC control file if exists
        if task.request_id:
            try:
                from semilabs_hone.core.ipc.paths import control_path, burn
                burn(control_path(task.request_id))
            except Exception:
                pass  # Ignore cleanup errors

        # Delete task (cascades to posts and comments via DB foreign key)
        sess.delete(task)
        sess.commit()

        return JSONResponse({"ok": True, "task_id": task_id})
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()


# ---------------------------------------------------------------------------
# Activate Browser (v2 块5, 风控弹窗唤起 worker Chrome)
# ---------------------------------------------------------------------------

@router.post("/api/tasks/{task_id}/activate-browser")
async def api_activate_browser(task_id: str) -> JSONResponse:
    """POST /api/tasks/{id}/activate-browser — activate Chrome window for manual intervention.

    Only allows for need_human tasks. Uses osascript to bring Chrome to front
    on macOS. Non-macOS systems return an error.
    """
    import subprocess
    import sys
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
        if not task:
            return JSONResponse({"ok": False, "error": "Task not found"}, status_code=404)

        if task.status != "need_human":
            return JSONResponse(
                {"ok": False, "error": "Task is not in need_human status"},
                status_code=409
            )

        # Check if running on macOS
        if sys.platform != "darwin":
            return JSONResponse(
                {"ok": False, "error": "activate-browser only supported on macOS"},
                status_code=501
            )

        # Use osascript to bring Chrome to front
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Google Chrome" to activate'],
                timeout=5,
                check=True
            )
            return JSONResponse({
                "ok": True,
                "task_id": task_id,
                "message": "Chrome activated"
            })
        except subprocess.TimeoutExpired:
            return JSONResponse(
                {"ok": False, "error": "Chrome activation timed out"},
                status_code=504
            )
        except subprocess.CalledProcessError as e:
            return JSONResponse(
                {"ok": False, "error": f"Chrome activation failed: {e}"},
                status_code=500
            )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()
