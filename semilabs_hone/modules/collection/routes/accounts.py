"""Account management routes — CRUD + login/validate/import-cookies.

Design: docs/ui_design_spec_v2.md §十三.
All long-running ops (login, validate) go through IPC client submit,
returning {request_id, status} so the frontend can track via WebSocket.

[契约变更 2026-07-13 S10]
- 建壳：nickname → remark（必填）
- login/validate/import 三路由 platform 从 Account.platform 读（去硬编码）
- DELETE 清 profile 目录 + cookies.json + 有 running 任务→拒绝
- 新增 GET /api/accounts (JSON) / GET /api/accounts/{id} (JSON)
- 新增 PUT /api/accounts/{id} (改 remark) / GET /api/accounts/{id}/edit (dialog)
- import-cookies 支持 conflict 响应（命中已存在 platform_user_id → 拒绝提示）
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

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


def _get_account_or_404(sess, account_id: int):
    from semilabs_hone.core.models.account import Account
    return sess.query(Account).filter(Account.id == account_id).first()


def _validate_cookie_json(cookies_str: str) -> tuple[list | None, str | None]:
    """校验 cookie JSON 字符串：解析 + 元素级校验。

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
        {
            "accounts": accounts,
            "platforms": platforms,
            "active_page": "auth",
        },
    )


# ---------------------------------------------------------------------------
# JSON APIs (供任务创建下拉 / 外部调用)
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
    from semilabs_hone.core.models.account import Account

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
    from semilabs_hone.modules.collection.browser.profile import profile_dir_for
    import os

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return HTMLResponse("<p>账号不存在</p>", status_code=404)

        # 读已绑定的 cookie 文件
        cookie_json = ""
        cookie_count = 0
        cookie_mtime = None
        cookie_path = profile_dir_for(account_id) / "cookies.json"
        if cookie_path.exists():
            try:
                with open(cookie_path, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    cookie_count = len(raw)
                    cookie_json = json.dumps(raw, ensure_ascii=False, indent=2)
                    mtime = os.path.getmtime(cookie_path)
                    cookie_mtime = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        t = _templates()
        assert t is not None
        return t.TemplateResponse(
            request, "_account_edit_dialog.html",
            {
                "account": a,
                "cookie_json": cookie_json,
                "cookie_count": cookie_count,
                "cookie_mtime": cookie_mtime,
            },
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
    """POST /api/accounts/{id}/edit — 统一更新备注 + cookie。

    - remark：同步写 DB（非空才更新）
    - cookies：非空且为合法 JSON 数组 → 提交 IPC cookie_import（异步验证）
    """
    from semilabs_hone.core.models.db import get_session

    # 1. 更新 remark（同步）
    remark_changed = False
    if remark and remark.strip():
        sess = get_session()
        try:
            a = _get_account_or_404(sess, account_id)
            if a is None:
                return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
            new_remark = remark.strip()[:100]
            if a.remark != new_remark:
                a.remark = new_remark
                sess.commit()
                remark_changed = True
            platform = a.platform
        finally:
            sess.close()
    else:
        # 只取 platform（cookie 提交需要）
        sess = get_session()
        try:
            a = _get_account_or_404(sess, account_id)
            if a is None:
                return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
            platform = a.platform
        finally:
            sess.close()

    # 2. 处理 cookie：严格校验 → 同步落盘 → 异步提交 IPC 验证
    cookie_saved = False
    cookie_submitted = False
    if cookies and cookies.strip():
        # 2a. 严格校验（JSON 解析 + 元素级 name/value/domain）
        cookies_data, err = _validate_cookie_json(cookies)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)

        # 2b. 同步落盘（立即可见，不依赖 worker）
        from semilabs_hone.modules.collection.browser.profile import profile_dir_for
        cookie_dir = profile_dir_for(account_id)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        cookie_path = cookie_dir / "cookies.json"
        with open(cookie_path, "w") as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)
        cookie_saved = True

        # 2c. 异步提交 IPC 验证（worker 有 ctx 时注入+验证+提取身份）
        IPCClient, IPCRequest = _ipc_client()
        req_id = uuid.uuid4().hex[:12]
        req = IPCRequest(
            request_id=req_id,
            module="collection",
            op="login",
            account_id=account_id,
            payload={
                "account_id": account_id,
                "platform": platform,
                "method": "cookie_import",
                "cookies": cookies_data,
                "request_id": req_id,
            },
        )
        client = IPCClient()
        client.submit(req)
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
    from semilabs_hone.modules.collection.browser.profile import profile_dir_for
    import os

    sess = get_session()
    try:
        a = _get_account_or_404(sess, account_id)
        if a is None:
            return HTMLResponse("<p>账号不存在</p>", status_code=404)

        # 读已绑定的 cookie 文件
        cookie_json = ""
        cookie_count = 0
        cookie_mtime = None
        cookie_path = profile_dir_for(account_id) / "cookies.json"
        if cookie_path.exists():
            try:
                with open(cookie_path, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    cookie_count = len(raw)
                    cookie_json = json.dumps(raw, ensure_ascii=False, indent=2)
                    mtime = os.path.getmtime(cookie_path)
                    cookie_mtime = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        t = _templates()
        assert t is not None
        return t.TemplateResponse(
            request, "_account_update_cookie_dialog.html",
            {
                "account": a,
                "cookie_json": cookie_json,
                "cookie_count": cookie_count,
                "cookie_mtime": cookie_mtime,
            },
        )
    finally:
        sess.close()


@router.post("/api/accounts/{account_id}/update-cookie")
async def api_update_cookie(
    request: Request,
    account_id: int,
    cookies: str = Form(default=""),
) -> JSONResponse:
    """POST /api/accounts/{id}/update-cookie — 更新指定账号的 cookie。

    [方案A重构] 替代原全局 import-cookies 端点，改为行级操作。
    """
    # 解析 cookies JSON
    try:
        cookies_data = json.loads(cookies) if cookies else []
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Cookie JSON 格式错误"}, status_code=400)

    if not isinstance(cookies_data, list) or not cookies_data:
        return JSONResponse({"ok": False, "error": "Cookie 必须是非空 JSON 数组"}, status_code=400)

    # 取 platform（从 Account 读）
    from semilabs_hone.core.models.db import get_session
    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        platform = acct.platform
    finally:
        sess.close()

    # 提交 IPC
    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]
    req = IPCRequest(
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
    )
    client = IPCClient()
    client.submit(req)

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
    from semilabs_hone.core.models.account import Account

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
# Mutation APIs
# ---------------------------------------------------------------------------

@router.post("/api/accounts")
async def api_create_account(
    request: Request,
    platform: str = Form(default="xiaohongshu"),
    remark: str = Form(default=""),
    nickname: str = Form(default=""),  # 兼容旧 form（前端已切 remark）
    cookies: str = Form(default=""),  # 可选，有则建壳后导入 cookie
) -> RedirectResponse:
    """POST /api/accounts — 建账号（空壳或建壳+导入 cookie）。

    [契约变更 2026-07-13 S10] remark NOT NULL 必填（用户裁决）。
    方案A重构：合并原"添加账号"和"导入cookie"为一步，cookie 为可选字段。
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account

    final_remark = (remark or "").strip() or (nickname or "").strip()
    if not final_remark:
        return RedirectResponse(url="/accounts?error=remark_required", status_code=303)

    sess = get_session()
    try:
        acct = Account(platform=platform, remark=final_remark[:100])
        sess.add(acct)
        sess.commit()
        new_id = acct.id
    finally:
        sess.close()

    # 如果提供了 cookies，校验 → 同步落盘 → 异步 IPC 验证
    if cookies and cookies.strip():
        cookies_data, err = _validate_cookie_json(cookies)
        if err:
            # cookie 格式错：建壳已成功，重定向带具体原因（URL encode，前端 Toast）
            from urllib.parse import quote
            return RedirectResponse(url=f"/accounts?cookie_error={quote(err)}", status_code=303)

        # 同步落盘
        from semilabs_hone.modules.collection.browser.profile import profile_dir_for
        cookie_dir = profile_dir_for(new_id)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        with open(cookie_dir / "cookies.json", "w") as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)

        # 异步 IPC 验证
        IPCClient, IPCRequest = _ipc_client()
        req_id = uuid.uuid4().hex[:12]
        req = IPCRequest(
            request_id=req_id,
            module="collection",
            op="login",
            account_id=new_id,
            payload={
                "account_id": new_id,
                "platform": platform,
                "method": "cookie_import",
                "cookies": cookies_data,
                "request_id": req_id,
            },
        )
        client = IPCClient()
        client.submit(req)

    return RedirectResponse(url="/accounts", status_code=303)


@router.delete("/api/accounts/{account_id}")
async def api_delete_account(request: Request, account_id: int) -> JSONResponse:
    """DELETE /api/accounts/{id} — 删账号。

    [契约变更 2026-07-13 S10]
    - 有 running 任务挂该账号→拒绝删除（先停任务）
    - 清 profile 目录 + cookies.json
    - 删 DB 行
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.core.models.task import CollectionTask

    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

        # 检查是否有 running 任务挂该账号
        running_count = sess.query(CollectionTask).filter(
            CollectionTask.account_id == account_id,
            CollectionTask.status == "running",
        ).count()
        if running_count > 0:
            return JSONResponse(
                {"ok": False, "error": f"该账号有 {running_count} 个 running 任务，请先停止"},
                status_code=409,
            )

        # 清 profile 目录 + cookies.json
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
    """POST /api/accounts/{id}/login — start login via IPC.

    [契约变更 2026-07-13 S10] platform 从 Account.platform 读（去硬编码）。
    Returns {request_id, status} — frontend tracks via WS.
    """
    from semilabs_hone.core.models.db import get_session

    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        platform = acct.platform
    finally:
        sess.close()

    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="login",
        account_id=account_id,
        payload={"account_id": account_id, "platform": platform, "method": "auto", "request_id": request_id},
    )

    client = IPCClient()
    client.submit(req)

    # L13: ensure a worker is alive to drive the login flow.
    spawner = getattr(request.app.state, "worker_spawner", None)
    if spawner is not None:
        try:
            spawner(account_id)
        except Exception:
            pass

    return JSONResponse({"request_id": request_id, "status": "submitted"})


@router.post("/api/accounts/import-cookies")
async def api_import_cookies(
    request: Request,
    account_id: int = Form(default=0),
    cookies: str = Form(default=""),
) -> JSONResponse:
    """POST /api/accounts/import-cookies — import cookies via IPC.

    [契约变更 2026-07-13 S10]
    - platform 从 Account.platform 读（去硬编码）
    - 命中已存在 platform_user_id → 返回 conflict（不静默合并）
    - JSON 响应（前端根据 status 展示 Toast 或冲突提示）
    """
    from semilabs_hone.core.models.db import get_session

    # 解析 cookies JSON（前置校验，避免 IPC 里再错）
    try:
        cookies_data = json.loads(cookies) if cookies else []
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Cookie JSON 格式错误"}, status_code=400)

    if not isinstance(cookies_data, list):
        return JSONResponse({"ok": False, "error": "Cookie 必须是 JSON 数组"}, status_code=400)

    if account_id <= 0:
        return JSONResponse({"ok": False, "error": "请选择账号"}, status_code=400)

    # 取 platform（从 Account 读）
    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        platform = acct.platform
    finally:
        sess.close()

    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
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
    )

    client = IPCClient()
    result = client.submit(req) or {}

    # conflict 透传（handler 内部已做 UNIQUE 检查 + 拒绝）
    if isinstance(result, dict) and result.get("status") == "conflict":
        return JSONResponse({
            "ok": False,
            "status": "conflict",
            "existing_id": result.get("existing_id"),
            "error": f"该平台账号已存在为账号 #{result.get('existing_id')}，是否更新它的 cookie？",
        }, status_code=409)

    return JSONResponse({"request_id": request_id, "status": "submitted", "ok": True})


@router.post("/api/accounts/{account_id}/validate")
async def api_validate_account(request: Request, account_id: int) -> JSONResponse:
    """POST /api/accounts/{id}/validate — validate session via IPC.

    [契约变更 2026-07-13 S10] platform 从 Account.platform 读（去硬编码）。
    Returns {request_id, status}.
    """
    from semilabs_hone.core.models.db import get_session

    sess = get_session()
    try:
        acct = _get_account_or_404(sess, account_id)
        if acct is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        platform = acct.platform
    finally:
        sess.close()

    IPCClient, IPCRequest = _ipc_client()
    request_id = uuid.uuid4().hex[:12]

    req = IPCRequest(
        request_id=request_id,
        module="collection",
        op="validate",
        account_id=account_id,
        payload={"account_id": account_id, "platform": platform, "request_id": request_id},
    )

    client = IPCClient()
    client.submit(req)

    return JSONResponse({"request_id": request_id, "status": "submitted"})
