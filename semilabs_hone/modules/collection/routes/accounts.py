"""Account management routes — CRUD + login / validate / import-cookies.

Design: docs/skim_design.md §13.1, docs/USER_SOP.md S2–S4, ui_design_spec_v2 §十三.

Rules that come from walking the user SOP:
- The platform of a long-running op is the **account's** platform, never a
  hardcoded default (G2) — one account belongs to exactly one platform.
- Creating an account assigns its one-account-one-fixed fingerprint
  (Layer 2, FIX_PLAN F6) plus its own Chrome profile dir.
- Long ops (login/validate) return ``{request_id, status}`` and stream
  their real outcome over WebSocket; the web process pulls the account's
  worker up on demand (L13) via ``app.state.worker_spawner``.

[契约变更 2026-07-13 S10 + v2 移植调和]
- 建壳：nickname → remark（必填；旧 form 字段 nickname 仍兼容读取）
- 页面契约切换：create → 303 重定向（v2 页整页表单）；delete/update → JSON
- 新增 GET /api/accounts (JSON) / GET /api/accounts/{id} (JSON)
- 新增 GET/POST /api/accounts/{id}/edit（备注+cookie 统一编辑 dialog）
- 新增 GET/POST /api/accounts/{id}/update-cookie[-dialog]（行级 cookie 更新）
- 新增 PUT /api/accounts/{id}（仅改 remark；平台身份系统自动提取不可手改）
- cookie 严格校验（_validate_cookie_json）+ 冲突经 WS cookie_import_conflict 透出
- DELETE 清 profile 目录；同平台有排队/运行中任务 → 409（PRD 无 task→account
  FK，worker 按平台解析 active 账号，故按平台保守拒绝，v2 原按 account_id 列）
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

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


def _get_account_or_404(sess, account_id: int):
    from semilabs_hone.core.models.account import Account
    return sess.query(Account).filter(Account.id == account_id).first()


def _validate_cookie_json(cookies_str: str) -> tuple[list | None, str | None]:
    """校验 cookie JSON 字符串：解析 + 元素级校验（v2 S10）。

    Returns:
        (cookies_list, None) 校验通过
        (None, error_msg) 校验失败，error_msg 含具体原因（前端展示）
    """
    try:
        cookies_data = json.loads(cookies_str)
    except json.JSONDecodeError as e:
        return None, f"Cookie JSON 格式错误：{e}"
    if not isinstance(cookies_data, list):
        return None, "Cookie 必须是 JSON 数组，如 [{\"name\":\"...\",\"value\":\"...\",\"domain\":\"...\"}]"
    if not cookies_data:
        return None, "Cookie 数组不能为空"
    for i, c in enumerate(cookies_data):
        if not isinstance(c, dict):
            return None, f"第 {i+1} 条 cookie 不是对象（缺少 name/value）"
        if not c.get("name"):
            return None, f"第 {i+1} 条 cookie 缺少 name 字段"
        if "value" not in c:
            return None, f"第 {i+1} 条 cookie 缺少 value 字段"
        if not c.get("domain"):
            return None, f"第 {i+1} 条 cookie 缺少 domain 字段（Playwright 注入需要）"
    return cookies_data, None


def _read_cookie_echo(account_id: int) -> dict:
    """读账号已绑定 cookie 文件，供编辑/更新 dialog 回显（v2 S10）。"""
    from semilabs_hone.modules.collection.browser.profile import profile_dir_for
    import os

    echo = {"cookie_json": "", "cookie_count": 0, "cookie_mtime": None}
    cookie_path = profile_dir_for(account_id) / "cookies.json"
    if cookie_path.exists():
        try:
            with open(cookie_path, "r") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                echo["cookie_count"] = len(raw)
                echo["cookie_json"] = json.dumps(raw, ensure_ascii=False, indent=2)
                mtime = os.path.getmtime(cookie_path)
                echo["cookie_mtime"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return echo


def _submit_cookie_import(request: Request, account_id: int, platform: str,
                          cookies_data: list) -> str:
    """提交 cookie_import IPC 并拉起 worker（L13）；返回 request_id。"""
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
            "cookies": cookies_data,
            "request_id": request_id,
        },
    ))
    _spawn_worker(request, account_id)
    return request_id


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


@router.get("/accounts", response_class=HTMLResponse)
async def page_accounts(request: Request) -> HTMLResponse:
    """GET /accounts — account list + add form（v2 暗色页，active_page='auth'）。"""
    from semilabs_hone.modules.collection.scrapers.registry import list_platforms

    t = _templates()
    assert t is not None, "Templates not initialized"
    return t.TemplateResponse(
        request, "accounts.html",
        {
            "accounts": _list_accounts(),
            "platforms": list_platforms(),
            "active_page": "auth",
        },
    )


# ---------------------------------------------------------------------------
# JSON APIs (v2 S10: 供下拉 / 外部调用 / 行级编辑)
# ---------------------------------------------------------------------------

@router.get("/api/accounts")
async def api_list_accounts() -> JSONResponse:
    """GET /api/accounts — JSON 列表，供下拉 / 任务创建页选账号（顺手收 L04）。"""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    sess = get_session()
    try:
        accounts = sess.query(Account).order_by(Account.id.desc()).all()
        out = []
        for a in accounts:
            out.append({
                "id": a.id,
                "platform": a.platform,
                "remark": a.remark,
                "platform_user_id": a.platform_user_id,
                "platform_nickname": a.platform_nickname,
                "status": a.status,
                "last_login_at": a.last_login_at.isoformat() if a.last_login_at else None,
                "fail_count": a.fail_count,
            })
        return JSONResponse(out)
    finally:
        sess.close()


@router.get("/api/accounts/{account_id}")
async def api_get_account(account_id: int) -> JSONResponse:
    """GET /api/accounts/{id} — JSON 详情。"""
    from semilabs_hone.core.models.db import get_session

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({
            "ok": True,
            "id": a.id,
            "platform": a.platform,
            "remark": a.remark,
            "platform_user_id": a.platform_user_id,
            "platform_nickname": a.platform_nickname,
            "status": a.status,
            "last_login_at": a.last_login_at.isoformat() if a.last_login_at else None,
            "fail_count": a.fail_count,
        })
    finally:
        sess.close()


@router.get("/api/accounts/{account_id}/edit", response_class=HTMLResponse)
async def api_edit_account_dialog(request: Request, account_id: int) -> HTMLResponse:
    """GET /api/accounts/{id}/edit — 统一编辑 dialog partial（备注 + cookie 回显）。"""
    from semilabs_hone.core.models.db import get_session

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return HTMLResponse("<p>账号不存在</p>", status_code=404)

        t = _templates()
        assert t is not None, "Templates not initialized"
        return t.TemplateResponse(
            request, "_account_edit_dialog.html",
            {"account": a, **_read_cookie_echo(account_id)},
        )
    finally:
        sess.close()


@router.post("/api/accounts/{account_id}/edit")
async def api_edit_account(
    request: Request,
    account_id: int,
    remark: str = Form(default=""),
    cookies: str = Form(default=""),
) -> JSONResponse:
    """POST /api/accounts/{id}/edit — 统一更新备注 + cookie（v2 S10）。

    - remark：同步写 DB（非空才更新）
    - cookies：非空且为合法 JSON 数组 → 同步落盘 + 提交 IPC cookie_import（异步验证）
    """
    from semilabs_hone.core.models.db import get_session

    # 1. 更新 remark（同步）
    remark_changed = False
    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        if remark and remark.strip():
            new_remark = remark.strip()[:100]
            if a.remark != new_remark:
                a.remark = new_remark
                sess.commit()
                remark_changed = True
        platform = a.platform
    finally:
        sess.close()

    # 2. 处理 cookie：严格校验 → 同步落盘 → 异步提交 IPC 验证
    cookie_saved = False
    cookie_submitted = False
    if cookies and cookies.strip():
        cookies_data, err = _validate_cookie_json(cookies)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)

        from semilabs_hone.modules.collection.browser.profile import profile_dir_for
        cookie_dir = profile_dir_for(account_id)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        with open(cookie_dir / "cookies.json", "w") as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)
        cookie_saved = True

        _submit_cookie_import(request, account_id, platform, cookies_data)
        cookie_submitted = True

    msg_parts = []
    if remark_changed:
        msg_parts.append("备注已更新")
    if cookie_saved:
        msg_parts.append("Cookie 已保存")
    if cookie_submitted:
        msg_parts.append("验证中")
    if not msg_parts:
        msg_parts.append("无变更")
    return JSONResponse({
        "ok": True,
        "message": "，".join(msg_parts),
        "remark_changed": remark_changed,
        "cookie_saved": cookie_saved,
        "cookie_submitted": cookie_submitted,
    })


@router.get("/api/accounts/{account_id}/update-cookie-dialog", response_class=HTMLResponse)
async def api_update_cookie_dialog(request: Request, account_id: int) -> HTMLResponse:
    """GET /api/accounts/{id}/update-cookie-dialog — 更新 cookie dialog partial。

    读取已绑定的 cookie 文件内容回显到 textarea，让用户看到当前 cookie。
    """
    from semilabs_hone.core.models.db import get_session

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return HTMLResponse("<p>账号不存在</p>", status_code=404)

        t = _templates()
        assert t is not None, "Templates not initialized"
        return t.TemplateResponse(
            request, "_account_update_cookie_dialog.html",
            {"account": a, **_read_cookie_echo(account_id)},
        )
    finally:
        sess.close()


@router.post("/api/accounts/{account_id}/update-cookie")
async def api_update_cookie(
    request: Request,
    account_id: int,
    cookies: str = Form(default=""),
) -> JSONResponse:
    """POST /api/accounts/{id}/update-cookie — 更新指定账号的 cookie（行级操作）。

    [v2 方案A] 替代原全局 import-cookies 表单流，改为行级操作；
    严格校验 → 同步落盘 → IPC 异步注入验证（提取平台身份回写）。
    """
    cookies_data, err = _validate_cookie_json(cookies or "")
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    platform = _account_platform(account_id)
    if platform is None:
        return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)

    from semilabs_hone.modules.collection.browser.profile import profile_dir_for
    cookie_dir = profile_dir_for(account_id)
    cookie_dir.mkdir(parents=True, exist_ok=True)
    with open(cookie_dir / "cookies.json", "w") as f:
        json.dump(cookies_data, f, ensure_ascii=False, indent=2)

    request_id = _submit_cookie_import(request, account_id, platform, cookies_data)
    return JSONResponse({"ok": True, "request_id": request_id, "status": "submitted"})


@router.put("/api/accounts/{account_id}")
async def api_update_account(
    request: Request,
    account_id: int,
    remark: str | None = Form(default=None),
) -> JSONResponse:
    """PUT /api/accounts/{id} — 改 remark（仅改备注，不改平台身份）。

    [契约变更 2026-07-13 S10] 用户裁决：platform_user_id/platform_nickname 由系统
    自动提取，不可手改；只有 remark 是用户可编辑的。
    """
    from semilabs_hone.core.models.db import get_session

    # 兼容 JSON body（PUT 表单少用，测试用 JSON 更方便）
    if remark is None:
        try:
            body = await request.json()
            remark = body.get("remark")
        except Exception:
            return JSONResponse({"ok": False, "error": "remark 必填"}, status_code=400)

    if not remark or not remark.strip():
        return JSONResponse({"ok": False, "error": "remark 必填"}, status_code=400)

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        a.remark = remark.strip()[:100]
        sess.commit()
        return JSONResponse({"ok": True, "id": a.id, "remark": a.remark})
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@router.post("/api/accounts")
async def api_create_account(
    request: Request,
    platform: str = Form(default="xiaohongshu"),
    remark: str = Form(default=""),
    nickname: str = Form(default=""),  # 兼容旧 form（前端已切 remark）
    cookies: str = Form(default=""),  # 可选，有则建壳后导入 cookie
) -> RedirectResponse:
    """POST /api/accounts — 建账号（空壳或建壳+导入 cookie），303 回 /accounts。

    [契约变更 2026-07-13 S10] remark NOT NULL 必填（用户裁决）；
    [v2 方案A] 合并"添加账号"和"导入cookie"为一步，cookie 为可选字段。
    保留 main F6：建壳即分配一账号一固定指纹 + 独立 Chrome profile dir。
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.modules.collection.anti_detect.fingerprint import assign_fingerprint
    from semilabs_hone.modules.collection.browser.profile import ensure_profile

    final_remark = (remark or "").strip() or (nickname or "").strip()
    if not final_remark:
        return RedirectResponse(url="/accounts?error=remark_required", status_code=303)

    sess = get_session()
    try:
        acct = Account(platform=platform, remark=final_remark[:100])
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
        new_id = acct.id
    finally:
        sess.close()

    # 可选 cookie：严格校验 → 同步落盘 → 异步 IPC 验证（提取平台身份）
    if cookies and cookies.strip():
        cookies_data, err = _validate_cookie_json(cookies)
        if err:
            # cookie 格式错：建壳已成功，重定向带具体原因（前端 Toast）
            from urllib.parse import quote
            return RedirectResponse(url=f"/accounts?cookie_error={quote(err)}", status_code=303)

        from semilabs_hone.modules.collection.browser.profile import profile_dir_for
        cookie_dir = profile_dir_for(new_id)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        with open(cookie_dir / "cookies.json", "w") as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)

        _submit_cookie_import(request, new_id, platform, cookies_data)

    return RedirectResponse(url="/accounts", status_code=303)


@router.delete("/api/accounts/{account_id}")
async def api_delete_account(request: Request, account_id: int) -> JSONResponse:
    """DELETE /api/accounts/{id} — 删账号（v2 S10：JSON 契约 + 清 profile）。

    守卫：同平台存在排队/运行中任务 → 409。PRD 无 task→account FK（worker 按
    平台解析 active 账号），无法精确归因，故按平台保守拒绝——先停任务再删。
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

        busy = sess.query(CollectionTask).filter(
            CollectionTask.platform == acct.platform,
            CollectionTask.status.in_(("pending", "running")),
        ).count()
        if busy > 0:
            return JSONResponse(
                {"ok": False,
                 "error": f"该平台有 {busy} 个排队/运行中任务，删除账号可能导致任务失败，请先停止"},
                status_code=409,
            )

        # 清 profile 目录（含 cookies.json）
        try:
            from semilabs_hone.modules.collection.browser.profile import profile_dir_for
            import shutil
            profile_dir = profile_dir_for(account_id)
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass

        sess.delete(acct)
        sess.commit()
        return JSONResponse({"ok": True})
    except Exception as exc:
        sess.rollback()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        sess.close()


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

    Legacy 全局导入入口（v2 页面已改用行级 edit/update-cookie；保留兼容）。
    The worker writes them into the account's real Chrome profile
    (``ctx.add_cookies``) and re-validates the session (FIX_PLAN F5)；
    身份冲突经 WS ``cookie_import_conflict`` 事件透出（文件 IPC 无同步回执）。
    """
    platform = _account_platform(account_id)
    if platform is None:
        return JSONResponse(
            {"ok": False, "error": f"账号 {account_id} 不存在",
             "fix_hint": "先添加账号，再导入该账号的 Cookie"},
            status_code=404,
        )

    cookies_data, err = _validate_cookie_json(cookies or "")
    if err:
        return JSONResponse(
            {"ok": False, "error": err,
             "fix_hint": '需要形如 [{"name":"a","value":"b","domain":".example.com","path":"/"}] 的非空数组'},
            status_code=400,
        )

    request_id = _submit_cookie_import(request, account_id, platform, cookies_data)

    return JSONResponse({
        "ok": True, "request_id": request_id,
        "platform": platform, "status": "submitted",
    })
