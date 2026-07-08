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
    """POST /api/tasks — create scrape task and start via IPC.

    Checks: only 1 running task at a time.
    Returns {request_id, status, task_id}.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import ScrapeTask, TaskKeyword
    from semilabs_hone.core.models.keyword import Keyword

    # Check no running task
    sess = get_session()
    try:
        running = sess.query(ScrapeTask).filter(
            ScrapeTask.status.in_(["pending", "running"])
        ).first()
        if running:
            return JSONResponse(
                {"ok": False, "error": "A task is already running"},
                status_code=409,
            )

        # Create task
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

        sess.commit()
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()

    # Update task to running
    sess = get_session()
    try:
        task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
        if task:
            task.status = "running"
            sess.commit()
    finally:
        sess.close()

    # Submit IPC request
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
        "status": "submitted",
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

        # Check no other running task
        other_running = sess.query(ScrapeTask).filter(
            ScrapeTask.id != task_id,
            ScrapeTask.status.in_(["pending", "running"]),
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
