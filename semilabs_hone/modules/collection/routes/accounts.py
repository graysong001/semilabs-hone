"""Account management routes — CRUD + login/validate/import-cookies.

Design: docs/skim_design.md §13.1.
All long-running ops (login, validate) go through IPC client submit,
returning {request_id, status} so the frontend can track via WebSocket.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

router = APIRouter()


def _templates():
    """Get shared templates from dashboard module."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _ipc_client():
    """Lazy import to avoid circular deps."""
    from semilabs_hone.core.ipc.client import IPCClient
    from semilabs_hone.core.ipc.protocol import IPCRequest
    return IPCClient, IPCRequest


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request) -> HTMLResponse:
    """GET /accounts — list accounts page."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        accounts = sess.query(Account).order_by(Account.id.desc()).all()
    except Exception:
        accounts = []
    finally:
        sess.close()

    from semilabs_hone.modules.collection.scrapers.registry import list_platforms
    platforms = list_platforms()

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "accounts.html",
        {"accounts": accounts, "platforms": platforms},
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.post("/api/accounts")
async def api_create_account(
    request: Request,
    platform: str = Form(default="xiaohongshu"),
    nickname: str = Form(default=""),
) -> RedirectResponse:
    """POST /api/accounts — create account, redirect to /accounts."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        acct = Account(platform=platform, nickname=nickname or None)
        sess.add(acct)
        sess.commit()
        acct_id = acct.id
    finally:
        sess.close()

    return RedirectResponse(url="/accounts", status_code=303)


@router.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: int) -> JSONResponse:
    """DELETE /api/accounts/{id} — delete account."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        acct = sess.query(Account).filter(Account.id == account_id).first()
        if acct:
            sess.delete(acct)
            sess.commit()
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()


@router.post("/api/accounts/{account_id}/login")
async def api_login_account(account_id: int) -> JSONResponse:
    """POST /api/accounts/{id}/login — start login via IPC.

    Returns {request_id, status} — frontend tracks via WS.
    """
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="login",
        account_id=account_id,
        payload={"account_id": account_id, "platform": "xiaohongshu", "method": "auto", "request_id": request_id},
    )

    client = IPCClient()
    client.submit(req)

    return JSONResponse({"request_id": request_id, "status": "submitted"})


@router.post("/api/accounts/import-cookies")
async def api_import_cookies(
    request: Request,
    account_id: int = Form(default=0),
    cookies: str = Form(default=""),
) -> RedirectResponse:
    """POST /api/accounts/import-cookies — import cookies via IPC."""
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    # Parse cookies JSON
    import json
    try:
        cookies_data = json.loads(cookies) if cookies else []
    except json.JSONDecodeError:
        cookies_data = []

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="login",
        account_id=account_id,
        payload={
            "account_id": account_id,
            "platform": "xiaohongshu",
            "method": "cookie_import",
            "cookies": cookies_data,
        },
    )

    client = IPCClient()
    client.submit(req)

    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/api/accounts/{account_id}/validate")
async def api_validate_account(account_id: int) -> JSONResponse:
    """POST /api/accounts/{id}/validate — validate session via IPC.

    Returns {request_id, status}.
    """
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="validate",
        account_id=account_id,
        payload={"account_id": account_id, "platform": "xiaohongshu", "request_id": request_id},
    )

    client = IPCClient()
    client.submit(req)

    return JSONResponse({"request_id": request_id, "status": "submitted"})
