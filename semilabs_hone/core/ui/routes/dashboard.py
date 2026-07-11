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
    """Render global dashboard page."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    session = get_session()
    try:
        account_count = session.query(Account).count()
    except Exception:
        account_count = 0
    finally:
        session.close()

    assert _templates is not None, "Templates not initialized — call set_templates() first"
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {"account_count": account_count},
    )


@router.get("/api/heartbeat")
async def api_heartbeat() -> HTMLResponse:
    """GET /api/heartbeat — worker heartbeat indicator fragment (PRD §5.1.1).

    Polled by HTMX every 10s from base.html. <30s since last heartbeat → green
    dot + "引擎运行中"; ≥30s or absent → red dot + "后台引擎离线，请重启应用".
    """
    from semilabs_hone.core.ipc import paths as ipc_paths

    age = ipc_paths.heartbeat_age()
    if age is not None and age < 30:
        return HTMLResponse(
            '<span class="heartbeat-dot green"></span> 引擎运行中'
        )
    return HTMLResponse(
        '<span class="heartbeat-dot red"></span> 后台引擎离线，请重启应用'
    )
