"""Dashboard route — global home page for semilabs-hone.

GET / renders the dashboard with module overview.
Empty DB shows a "no accounts" guidance card.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Templates are set by create_app() at startup so that the shared
# environment (get_modules global) is available.
_templates: Jinja2Templates | None = None


def set_templates(templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """GET / — 采集任务大厅 (ui_design_spec_v2 §6).

    聚合统计卡片 (today_count / running_count / need_human_count / daily_limit /
    running_diff) + 任务分页 (最新在前)。
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.core.models.task import CollectionTask
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms
    from config import DAILY_LIMIT_PER_ACCOUNT
    from datetime import datetime, timezone
    from sqlalchemy import func

    open_create = request.query_params.get("openCreate", "").lower() == "true"
    new_platform = request.query_params.get("platform", "")

    sess = get_session()
    try:
        account_count = sess.query(Account).count()
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = (
            sess.query(func.sum(CollectionTask.actual_count))
            .filter(CollectionTask.created_at >= today_start)
            .scalar()
        ) or 0
        running_count = sess.query(CollectionTask).filter(CollectionTask.status == "running").count()
        need_human_count = sess.query(CollectionTask).filter(CollectionTask.status == "need_human").count()
        completed_today = (
            sess.query(CollectionTask)
            .filter(CollectionTask.created_at >= today_start, CollectionTask.status == "completed")
            .count()
        )
        running_diff = running_count - completed_today
        # 分页 (每页 10, 最新在前)
        try:
            page = max(1, int(request.query_params.get("page", "1")))
        except ValueError:
            page = 1
        per_page = 10
        total = sess.query(CollectionTask).count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        tasks = (
            sess.query(CollectionTask)
            .order_by(CollectionTask.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        page_start = (page - 1) * per_page + 1 if total else 0
        page_end = min(page * per_page, total)
    except Exception:
        account_count = 0
        today_count = 0
        running_count = 0
        need_human_count = 0
        running_diff = 0
        tasks = []
        page, total_pages, total, page_start, page_end = 1, 1, 0, 0, 0
    finally:
        sess.close()

    assert _templates is not None, "Templates not initialized — call set_templates() first"

    # 预计算 badge 和 actions HTML (避免模板里调用后端函数)
    from semilabs_hone.modules.collection.routes.tasks import _badge_html, _actions_html
    badge_map = {t.id: _badge_html(t) for t in tasks}
    actions_map = {t.id: _actions_html(t) for t in tasks}

    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "account_count": account_count,
            "active_page": "dashboard",
            "today_count": today_count,
            "running_count": running_count,
            "need_human_count": need_human_count,
            "daily_limit": DAILY_LIMIT_PER_ACCOUNT,
            "running_diff": running_diff,
            "tasks": tasks,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "page_start": page_start,
            "page_end": page_end,
            "range": range,
            "badge_map": badge_map,
            "actions_map": actions_map,
            "platforms": list_platforms(),
            "open_create_modal": open_create,
            "new_platform": new_platform,
        },
    )


@router.get("/api/heartbeat")
async def api_heartbeat() -> HTMLResponse:
    """GET /api/heartbeat — worker heartbeat indicator fragment (PRD §5.1.1).

    Polled by HTMX every 10s from base.html topbar pill. <30s since last
    heartbeat → green pulse dot + "Engine Online"; ≥30s or absent → red dot
    + "Engine Offline". 返回 Tailwind class 片段 (ui_design_spec_v2 §4 顶栏).
    """
    from semilabs_hone.core.ipc import paths as ipc_paths

    age = ipc_paths.heartbeat_age()
    if age is not None and age < 30:
        return HTMLResponse(
            '<span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>'
            '<span class="text-xs text-green-400">Engine Online</span>'
        )
    return HTMLResponse(
        '<span class="w-2 h-2 rounded-full bg-red-500"></span>'
        '<span class="text-xs text-red-400">Engine Offline</span>'
    )
