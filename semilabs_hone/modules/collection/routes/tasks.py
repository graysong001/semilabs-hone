"""Task management routes — create/list/cancel/resume.

Design: docs/skim_design.md §13.1.
- Platform dropdown from registry.list_platforms()
- Only 1 running task at a time (check DB)
- POST /api/tasks → IPC submit → {request_id, status}
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
async def page_task_detail(request: Request, task_id: int) -> HTMLResponse:
    """GET /tasks/{id} — task detail page with progress."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import ScrapeTask

    sess = get_session()
    try:
        task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
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
        {"task": task},
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/api/tasks")
async def api_create_task(
    request: Request,
    account_id: int = Form(default=0),
    platform: str = Form(default="xiaohongshu"),
    keywords: str = Form(default=""),
    sort: str = Form(default="general"),
    max_posts: int = Form(default=20),
    download_images: bool = Form(default=True),
    collect_comments: bool = Form(default=True),
) -> JSONResponse:
    """POST /api/tasks — create scrape task and enqueue via IPC.

    Single-running lock (PRD §8.2 场景 2.2): at most one task is `running` at a
    time. New tasks are always created as `pending`. When no task is currently
    `running`, this one is promoted to `running` and its IPC request is
    submitted ("submitted"). When another task is already `running`, the new
    task stays `pending` and its IPC request is still submitted so the worker
    picks it up in mtime order after the current one finishes ("queued").

    Returns {request_id, status, task_id}.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import ScrapeTask, TaskKeyword
    from semilabs_hone.core.models.keyword import Keyword

    sess = get_session()
    try:
        # Single-running lock: is a task already running?
        already_running = sess.query(ScrapeTask).filter(
            ScrapeTask.status == "running"
        ).first() is not None

        # Create task as pending (queueable — PRD §8.2 场景 2.2 allows B/C pending
        # while A runs).
        task = ScrapeTask(
            account_id=account_id,
            platform=platform,
            status="pending",
            max_posts_per_keyword=max_posts,
            sort_type=sort,
            download_images=download_images,
            collect_comments=collect_comments,
        )
        sess.add(task)
        sess.flush()
        task_id = task.id

        # Upsert keywords and link
        for kw_text in [k.strip() for k in keywords.split(",") if k.strip()]:
            kw = (
                sess.query(Keyword)
                .filter(Keyword.text == kw_text, Keyword.platform == platform)
                .first()
            )
            if not kw:
                kw = Keyword(text=kw_text, platform=platform)
                sess.add(kw)
                sess.flush()
            link = TaskKeyword(task_id=task_id, keyword_id=kw.id)
            sess.add(link)

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

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="scrape_task",
        account_id=account_id,
        payload={
            "task_id": task_id,
            "platform": platform,
            "keywords": [k.strip() for k in keywords.split(",") if k.strip()],
            "sort": sort,
            "max_posts_per_keyword": max_posts,
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
async def api_cancel_task(task_id: int) -> JSONResponse:
    """POST /api/tasks/{id}/cancel — cancel running task."""
    IPCClient, _ = _ipc_client()
    # Cancel is done via IPC client cancel method
    # We also update DB status
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import ScrapeTask

    sess = get_session()
    try:
        task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
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
async def api_resume_task(task_id: int) -> JSONResponse:
    """POST /api/tasks/{id}/resume — resume failed task from checkpoint."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import ScrapeTask

    sess = get_session()
    try:
        task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
        if not task:
            return JSONResponse({"ok": False, "error": "Task not found"}, status_code=404)

        # Single-running lock: only reject if ANOTHER task is already running
        # (a pending task does not block resuming this one — PRD §8.2 场景 2.2).
        other_running = sess.query(ScrapeTask).filter(
            ScrapeTask.id != task_id,
            ScrapeTask.status == "running",
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
