"""Account management routes — CRUD + login / validate / import-cookies.

Design: docs/skim_design.md §13.1, docs/USER_SOP.md S2–S4.

Rules that come from walking the user SOP:
- The platform of a long-running op is the **account's** platform, never a
  hardcoded default (G2) — one account belongs to exactly one platform.
- Write operations answer with the accounts-table fragment, because the
  page drives them through HTMX and swaps that fragment in place (G3).
- Creating an account assigns its one-account-one-fixed fingerprint
  (Layer 2, FIX_PLAN F6) plus its own Chrome profile dir.
- Long ops (login/validate) return ``{request_id, status}`` and stream
  their real outcome over WebSocket; the web process pulls the account's
  worker up on demand (L13) via ``app.state.worker_spawner``.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

router = APIRouter()


def _templates():
    """Shared Jinja environment (set up by create_app)."""
    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    return dash_mod._templates


def _ipc() -> tuple:
    """Lazy import to avoid circular deps."""
    from semilabs_hone.core.ipc.client import IPCClient
    from semilabs_hone.core.ipc.protocol import IPCRequest
    return IPCClient, IPCRequest


def _spawn_worker(request: Request, account_id: int) -> None:
    """Best-effort worker pull-up (L13); no-op when auto-spawn is off (tests)."""
    spawner = getattr(request.app.state, "worker_spawner", None)
    if spawner is not None:
        try:
            spawner(account_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _list_accounts() -> list:
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        return sess.query(Account).order_by(Account.id.desc()).all()
    finally:
        sess.close()


def _account_platform(account_id: int) -> str | None:
    """Platform of one account, or None when the account does not exist (G2)."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        acct = sess.query(Account).filter(Account.id == account_id).first()
        return acct.platform if acct else None
    finally:
        sess.close()


def _render_accounts_table(request: Request) -> HTMLResponse:
    """Render just the accounts table (HTMX swap target, G3)."""
    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "_accounts_table.html", {"accounts": _list_accounts()},
    )


@router.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request) -> HTMLResponse:
    """GET /accounts — account list + add/import forms."""
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "accounts.html",
        {"accounts": _list_accounts(), "platforms": list_platforms()},
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@router.post("/api/accounts", response_class=HTMLResponse)
async def api_create_account(
    request: Request,
    platform: str = Form(default="xiaohongshu"),
    nickname: str = Form(default=""),
) -> HTMLResponse:
    """POST /api/accounts — create an account, answer with the table fragment.

    Assigns the one-account-one-fixed fingerprint (Layer 2, FIX_PLAN F6):
    a fresh random draw persisted on the row, plus its own Chrome profile.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.modules.collection.anti_detect.fingerprint import assign_fingerprint
    from semilabs_hone.modules.collection.browser.profile import ensure_profile

    sess = get_session()
    try:
        acct = Account(platform=platform, nickname=nickname or None)
        sess.add(acct)
        sess.flush()  # need the id for the profile dir
        fp = assign_fingerprint()
        acct.viewport_w = fp.viewport["width"]
        acct.viewport_h = fp.viewport["height"]
        acct.color_scheme = fp.color_scheme
        acct.timezone = fp.timezone
        acct.locale = fp.locale
        acct.profile_dir = str(ensure_profile(acct.id))
        sess.commit()
    finally:
        sess.close()

    return _render_accounts_table(request)


@router.delete("/api/accounts/{account_id}", response_class=HTMLResponse)
async def api_delete_account(request: Request, account_id: int) -> HTMLResponse:
    """DELETE /api/accounts/{id} — delete, answer with the table fragment."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        acct = sess.query(Account).filter(Account.id == account_id).first()
        if acct:
            sess.delete(acct)
            sess.commit()
    finally:
        sess.close()

    return _render_accounts_table(request)


@router.post("/api/accounts/{account_id}/login")
async def api_login_account(request: Request, account_id: int) -> JSONResponse:
    """POST /api/accounts/{id}/login — start login through the account's worker."""
    platform = _account_platform(account_id)
    if platform is None:
        return JSONResponse(
            {"ok": False, "error": f"账号 {account_id} 不存在",
             "fix_hint": "先在账号页添加账号"},
            status_code=404,
        )

    IPCClient, IPCRequest = _ipc()
    request_id = uuid.uuid4().hex[:12]
    IPCClient().submit(IPCRequest(
        request_id=request_id,
        module="collection",
        op="login",
        account_id=account_id,
        payload={
            "account_id": account_id,
            "platform": platform,
            "method": "auto",
            "request_id": request_id,
        },
    ))
    _spawn_worker(request, account_id)

    return JSONResponse({
        "ok": True, "request_id": request_id,
        "platform": platform, "status": "submitted",
    })


@router.post("/api/accounts/{account_id}/validate")
async def api_validate_account(request: Request, account_id: int) -> JSONResponse:
    """POST /api/accounts/{id}/validate — check the live session via the worker."""
    platform = _account_platform(account_id)
    if platform is None:
        return JSONResponse(
            {"ok": False, "error": f"账号 {account_id} 不存在",
             "fix_hint": "先在账号页添加账号"},
            status_code=404,
        )

    IPCClient, IPCRequest = _ipc()
    request_id = uuid.uuid4().hex[:12]
    IPCClient().submit(IPCRequest(
        request_id=request_id,
        module="collection",
        op="validate",
        account_id=account_id,
        payload={
            "account_id": account_id,
            "platform": platform,
            "request_id": request_id,
        },
    ))
    _spawn_worker(request, account_id)

    return JSONResponse({
        "ok": True, "request_id": request_id,
        "platform": platform, "status": "submitted",
    })


@router.post("/api/accounts/import-cookies", response_model=None)
async def api_import_cookies(
    request: Request,
    account_id: int = Form(default=0),
    cookies: str = Form(default=""),
) -> Response:
    """POST /api/accounts/import-cookies — hand a cookie list to the worker.

    The worker writes them into the account's real Chrome profile
    (``ctx.add_cookies``) and re-validates the session (FIX_PLAN F5).
    """
    platform = _account_platform(account_id)
    if platform is None:
        return JSONResponse(
            {"ok": False, "error": f"账号 {account_id} 不存在",
             "fix_hint": "先添加账号，再导入该账号的 Cookie"},
            status_code=404,
        )

    try:
        cookie_list = json.loads(cookies) if cookies.strip() else []
    except json.JSONDecodeError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Cookie JSON 解析失败: {exc}",
             "fix_hint": '需要形如 [{"name":"a","value":"b","domain":".example.com","path":"/"}]'},
            status_code=400,
        )
    if not isinstance(cookie_list, list) or not cookie_list:
        return JSONResponse(
            {"ok": False, "error": "Cookie 列表为空",
             "fix_hint": "从浏览器导出该平台的 Cookie 数组后再粘贴"},
            status_code=400,
        )

    IPCClient, IPCRequest = _ipc()
    request_id = uuid.uuid4().hex[:12]
    IPCClient().submit(IPCRequest(
        request_id=request_id,
        module="collection",
        op="login",
        account_id=account_id,
        payload={
            "account_id": account_id,
            "platform": platform,
            "method": "cookie_import",
            "cookies": cookie_list,
            "request_id": request_id,
        },
    ))
    _spawn_worker(request, account_id)

    return _render_accounts_table(request)
