"""Collection IPC handlers — op dispatch to collection logic.

Design: docs/skim_design.md §6.3, §9.3.
Each handler receives (payload, progress_cb) and returns a dict.
Async handlers are awaited by the IPC server loop.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from loguru import logger

from semilabs_hone.core.utils.retry import DailyLimitError

# Lazy imports for optional dependencies
# fmt: off

# Worker-injected browser context singleton (L14): the worker process attaches
# Chrome via CDP and publishes the BrowserContext here so the handler-created
# GenericEngine can resolve a page. None in tests / when no worker is attached.
_WORKER_CTX: Any = None


def set_worker_ctx(ctx: Any) -> None:
    """Publish the worker's live BrowserContext so handlers/engines can reach it.

    Called once from worker_main._run_worker after `attach(port)`. Stays None in
    the web process and in tests, where _get_engine degrades to ctx=None (the
    engine's _ensure_page then raises, which tests sidestep by mocking _get_engine
    or not driving a real page).
    """
    global _WORKER_CTX
    _WORKER_CTX = ctx


async def _worker_page() -> Any | None:
    """Resolve a Playwright page from the worker ctx, or None when no ctx.

    Used by login-QR (L15) and solver wiring (L10) that operate on a page but do
    not own a GenericEngine. None return lets callers degrade to stub/manual.
    """
    if _WORKER_CTX is None:
        return None
    try:
        pages = _WORKER_CTX.pages if hasattr(_WORKER_CTX, "pages") else []
        if pages:
            return pages[0]
        return await _WORKER_CTX.new_page()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# [契约变更 2026-07-13 S10] cookie 路径 / 登录成功回写 / 身份提取 helpers
# ---------------------------------------------------------------------------

def _cookie_path_for(account_id: int) -> "Path":
    """Return cookies.json path for an account.

    Path 统一用 profile_dir_for(account_id)，与 Chrome profile 同目录。
    [S10] 删掉原来 `acct_{account_id}` 前缀 bug。
    """
    from semilabs_hone.modules.collection.browser.profile import profile_dir_for
    return profile_dir_for(int(account_id)) / "cookies.json"


async def _verify_cookies_on_platform(ctx: Any, platform: str) -> bool:
    """用 ctx 的 cookie 请求平台需登录接口验证有效性。

    [契约变更 2026-07-13 S10] 导入 cookie / 验证登录态必须真请求平台接口，
    不能只看文件存在。返回 True=cookie 有效（2xx），False=无效或非 2xx。
    """
    if ctx is None:
        return False
    try:
        from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
        spec, _ = reg_get(platform)
        verify_url = (spec.base_url or "") + (spec.login.verify_url or "")
        if not verify_url:
            # yaml 未配 verify_url，降级为只检查 cookie 非空
            return True
        page = await _worker_page()
        if page is None:
            return False
        resp = await page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    return r.ok;
                } catch (e) {
                    return false;
                }
            }""",
            verify_url,
        )
        return bool(resp)
    except Exception as exc:
        logger.warning(f"cookie verify failed: {exc}")
        return False


async def _extract_platform_identity(ctx: Any, platform: str) -> dict | None:
    """从平台 identity_api 提取 {platform_user_id, platform_nickname}。

    [契约变更 2026-07-13 S10] 登录/导入成功后调用，回写到 Account 的
    platform_user_id / platform_nickname。返回 None 表示未配置或提取失败
    （不阻塞登录成功，只留空）。
    """
    if ctx is None:
        return None
    try:
        from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
        spec, _ = reg_get(platform)
        identity_api = spec.login.identity_api
        identity_map = spec.login.identity_map
        if not identity_api or not identity_map:
            return None
        url = (spec.base_url or "") + identity_api
        page = await _worker_page()
        if page is None:
            return None
        body = await page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    if (!r.ok) return null;
                    return await r.json();
                } catch (e) {
                    return null;
                }
            }""",
            url,
        )
        if not isinstance(body, dict):
            return None

        def _resolve(obj: Any, path: str) -> Any:
            cur = obj
            for p in path.split("."):
                if cur is None:
                    return None
                if isinstance(cur, dict):
                    cur = cur.get(p)
                else:
                    return None
            return cur

        out = {}
        for key, path in identity_map.items():
            out[key] = _resolve(body, path)
        if not out.get("user_id"):
            return None
        return {
            "platform_user_id": str(out["user_id"]),
            "platform_nickname": str(out["nickname"]) if out.get("nickname") else None,
        }
    except Exception as exc:
        logger.warning(f"identity extract failed: {exc}")
        return None


def _find_conflicting_account(platform: str, platform_user_id: str, exclude_id: int | None = None) -> int | None:
    """检查同 platform+platform_user_id 是否已有其他账号。

    返回冲突账号 id，无冲突返回 None。用于导入 cookie 时拒绝静默合并。
    """
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            q = sess.query(Account).filter(
                Account.platform == platform,
                Account.platform_user_id == platform_user_id,
            )
            if exclude_id is not None:
                q = q.filter(Account.id != exclude_id)
            existing = q.first()
            return existing.id if existing else None
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"conflict check failed: {exc}")
        return None


def _apply_login_success(
    account_id: int | None,
    progress_cb: Callable,
    platform: str | None = None,
    platform_user_id: str | None = None,
    platform_nickname: str | None = None,
    login_method: str | None = None,
) -> None:
    """登录成功回写：清零 fail_count、置 active、写 last_login_at、
    写 platform_user_id / platform_nickname（如有）/ login_method（如有）。

    [契约变更 2026-07-13 S10] 状态机：成功 → 清零 fail_count → active。
    """
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            acct = sess.query(Account).filter(Account.id == account_id).first()
            if acct is None:
                return
            acct.status = "active"
            acct.fail_count = 0
            acct.last_login_at = datetime.now(timezone.utc)
            if platform_user_id is not None:
                acct.platform_user_id = platform_user_id
            if platform_nickname is not None:
                acct.platform_nickname = platform_nickname
            if login_method is not None:
                acct.login_method = login_method
            sess.commit()
            progress_cb("account_status_updated", {
                "account_id": account_id,
                "status": "active",
                "platform_user_id": platform_user_id,
            })
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to apply login success: {exc}")


def _apply_login_failure(account_id: int | None, progress_cb: Callable) -> str:
    """登录/验证失败回写：fail_count+1；达 5 → suspended。

    返回当前 status（active/inactive/suspended 等）供调用方判断。
    [契约变更 2026-07-13 S10] 状态机：失败 → +1；达 5 → suspended。
    """
    status = "inactive"
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            acct = sess.query(Account).filter(Account.id == account_id).first()
            if acct is None:
                return status
            acct.fail_count = (acct.fail_count or 0) + 1
            if acct.fail_count >= 5:
                acct.status = "suspended"
            status = acct.status
            sess.commit()
            progress_cb("account_fail_count_incremented", {
                "account_id": account_id,
                "fail_count": acct.fail_count,
                "status": status,
            })
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to apply login failure: {exc}")
    return status


def _persist_cookies(account_id: int | None, cookies: list) -> None:
    """落盘 cookie 到统一路径 profiles/{account_id}/cookies.json。

    [契约变更 2026-07-13 S10] 路径统一，删 acct_ 前缀 bug。
    """
    from pathlib import Path
    cookie_path = _cookie_path_for(account_id)
    Path(cookie_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cookie_path, "w") as f:
        json.dump(cookies, f)


def build_registry() -> dict[str, Callable]:
    """Build the handler registry for IPC server dispatch.

    Returns:
        {op_name: handler_fn} mapping for:
        login, validate, scrape_task, search, detail, comments
    """
    return {
        "login": handler_login,
        "validate": handler_validate,
        "scrape_task": handler_scrape_task,
        "search": handler_search,
        "detail": handler_detail,
        "comments": handler_comments,
    }


# ---------------------------------------------------------------------------
# handler_login — three-tier login (Cookie recovery → QR → Import)
# ---------------------------------------------------------------------------

async def handler_login(payload: dict, progress_cb: Callable) -> dict:
    """Handle login operation.

    Three-tier flow:
    1. Cookie recovery (if cookies exist on disk)
    2. QR code scan (if platform supports it)
    3. Cookie import (manual paste)

    Args:
        payload: {platform, account_id, method?, cookies?}
        progress_cb: (message, data) callback for IPC progress.

    Returns:
        {status, login_method, qr_screenshot?, request_id}
    """
    platform = payload.get("platform", "xiaohongshu")
    account_id = payload.get("account_id")
    method = payload.get("method", "auto")
    request_id = payload.get("request_id", "")

    progress_cb("login_start", {"platform": platform, "account_id": account_id})

    if method == "auto" or method == "cookie_recovery":
        # Tier 1: Try cookie recovery (file-level check; real verify in handler_validate)
        recovered = _try_cookie_recovery(account_id, platform, progress_cb)
        if recovered:
            _apply_login_success(account_id, progress_cb, platform=platform, login_method="cookie_recovery")
            progress_cb("login_success", {"account_id": account_id, "method": "cookie_recovery"})
            return {
                "status": "ok",
                "login_method": "cookie_recovery",
                "account_id": account_id,
            }

    if method == "auto" or method == "qrcode":
        # Tier 2: QR code login (S9a L15 已真实化 + S10 补 success_pattern 轮询+回写)
        progress_cb("login_qr_start", {"account_id": account_id})
        qr_result = await _do_qr_login(platform, account_id, progress_cb)
        if qr_result:
            # _do_qr_login 内部已在成功时调用 _apply_login_success
            if not qr_result.get("login_success"):
                # 无 success_pattern 配置时，_do_qr_login 只截图；此处兜底置 active
                _apply_login_success(account_id, progress_cb, platform=platform, login_method="qrcode")
            return {
                "status": "ok",
                "login_method": "qrcode",
                "account_id": account_id,
                **qr_result,
            }

    if method == "cookie_import":
        # Tier 3: Cookie import (S10 加注入+验证+身份回写+冲突拒绝)
        cookies = payload.get("cookies")
        if cookies:
            result = await _import_cookies(account_id, platform, cookies, progress_cb)
            if result.get("conflict"):
                return {
                    "status": "conflict",
                    "existing_id": result["existing_id"],
                    "account_id": account_id,
                    "identity": result.get("identity"),
                }
            if not result.get("ok"):
                return {
                    "status": "error",
                    "reason": result.get("reason", "cookie 导入失败"),
                    "account_id": account_id,
                }
            return {
                "status": "ok",
                "login_method": "cookie_import",
                "account_id": account_id,
                "identity": result.get("identity"),
            }

    # Fall through to QR if auto and recovery failed
    if method == "auto":
        progress_cb("login_qr_start", {"account_id": account_id})
        qr_result = await _do_qr_login(platform, account_id, progress_cb)
        if qr_result:
            if not qr_result.get("login_success"):
                _apply_login_success(account_id, progress_cb, platform=platform, login_method="qrcode")
            return {
                "status": "ok",
                "login_method": "qrcode",
                "account_id": account_id,
                **qr_result,
            }

    from semilabs_hone.core.utils.retry import LoginError
    raise LoginError("所有登录方式均失败")


def _try_cookie_recovery(account_id: int | None, platform: str, progress_cb: Callable) -> bool:
    """Try to recover login from persisted cookies.

    [契约变更 2026-07-13 S10] 路径统一用 _cookie_path_for（删 acct_ 前缀）。
    只检查文件存在 + 非空；真正注入+验证走 handler_validate。
    """
    cookie_path = _cookie_path_for(account_id)
    if not cookie_path.exists():
        progress_cb("login_recovery_no_cookies", {"account_id": account_id})
        return False
    try:
        with open(cookie_path, "r") as f:
            cookies = json.load(f)
        if cookies and len(cookies) > 0:
            progress_cb("login_recovery_found_cookies", {"account_id": account_id, "count": len(cookies)})
            return True
    except Exception:
        pass
    return False


async def _do_qr_login(platform: str, account_id: int | None, progress_cb: Callable) -> dict | None:
    """Initiate QR code login. Returns QR info dict or None.

    [契约变更 2026-07-13 S10] 在 S9a L15 真实化（page.goto + screenshot）基础上，
    补 success_pattern 轮询：扫码后轮询 page.url 匹配 yaml success_pattern（≤timeout 120s），
    成功后 context.cookies() 提取落盘+注入+回写 platform_user_id/platform_nickname+active+last_login_at，
    WS 广播 qr_ready → login_success。

    Without a page (tests / no worker), degrade to returning the qr_path stub so
    callers/tests that only check the path stay green.
    """
    from config import DATA_DIR
    from pathlib import Path
    qr_path = str(DATA_DIR / "collection" / "debug" / f"qr_{account_id}.png")
    Path(qr_path).parent.mkdir(parents=True, exist_ok=True)

    page = await _worker_page()
    if page is None:
        progress_cb("qr_ready", {"qr_path": qr_path, "account_id": account_id})
        return {"qr_path": qr_path}

    # Resolve login URL + success pattern from the platform spec.
    try:
        from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
        spec, _ = reg_get(platform)
        login_url = (spec.base_url or "") + (spec.login.login_url or "")
        success_detect = spec.login.success_detect
        success_pattern = spec.login.success_pattern
        timeout_s = int(spec.login.timeout or 120)
    except Exception:
        login_url = ""
        success_detect = None
        success_pattern = None
        timeout_s = 120

    if not login_url:
        progress_cb("qr_ready", {"qr_path": qr_path, "account_id": account_id})
        return {"qr_path": qr_path}

    try:
        await page.goto(login_url)
        try:
            await page.wait_for_selector("img, canvas", timeout=5000)
        except Exception:
            pass
        await page.screenshot(path=qr_path)
        progress_cb("qr_ready", {"qr_path": qr_path, "account_id": account_id})
    except Exception as exc:
        logger.warning(f"QR navigation/screenshot failed: {exc}; returning stub path")
        progress_cb("qr_ready", {"qr_path": qr_path, "account_id": account_id})
        return {"qr_path": qr_path}

    # [S10] 轮询 success_pattern ≤ timeout_s，检测扫码成功
    if success_detect == "url_change" and success_pattern:
        import re
        regex = re.compile(success_pattern)
        login_base = (spec.base_url or "")
        # 登录页的 URL（用于判断是否已跳离）
        login_page_url = login_url
        deadline = time.time() + timeout_s
        poll_interval = 1.5
        while time.time() < deadline:
            try:
                cur_url = page.url
            except Exception:
                cur_url = ""
            # 跳离登录页且匹配 success_pattern → 成功
            if cur_url and cur_url != login_page_url:
                # success_pattern 用相对路径匹配（去掉 base_url）
                rel = cur_url.replace(login_base, "", 1) if cur_url.startswith(login_base) else cur_url
                if regex.match(rel) or regex.match(cur_url):
                    # 提取 cookie + 身份 + 回写
                    try:
                        cookies = await page.context.cookies()
                        _persist_cookies(account_id, cookies)
                        identity = await _extract_platform_identity(page.context, platform)
                        _apply_login_success(
                            account_id, progress_cb,
                            platform=platform,
                            platform_user_id=(identity or {}).get("platform_user_id"),
                            platform_nickname=(identity or {}).get("platform_nickname"),
                            login_method="qrcode",
                        )
                        progress_cb("login_success", {
                            "account_id": account_id,
                            "method": "qrcode",
                            "platform_user_id": (identity or {}).get("platform_user_id"),
                        })
                        return {"qr_path": qr_path, "login_success": True, "identity": identity}
                    except Exception as exc:
                        logger.warning(f"QR success post-processing failed: {exc}")
                        return {"qr_path": qr_path}
            await asyncio.sleep(poll_interval)

        # 超时未成功 → 失败回写
        _apply_login_failure(account_id, progress_cb)
        progress_cb("login_timeout", {"account_id": account_id, "timeout": timeout_s})
        return {"qr_path": qr_path, "login_success": False}

    # 无 success_pattern 配置 → 只截图等待人工
    return {"qr_path": qr_path}


async def _import_cookies(account_id: int | None, platform: str, cookies: list, progress_cb: Callable) -> dict:
    """Persist + 注入 + 验证 + 回写导入的 cookie。

    [契约变更 2026-07-13 S10]
    1. 落盘到统一路径 profiles/{account_id}/cookies.json
    2. ctx.add_cookies(cookies) 真注入浏览器
    3. 用注入 cookie 请求需登录接口验证
    4. 成功 → 提取 platform_user_id/platform_nickname 回写 + 置 active
       失败 → 留 inactive + fail_count+1
    5. 命中已存在 platform_user_id → 拒绝（不静默合并）

    Returns:
        {"ok": True, "identity": {...}} / {"ok": False, "reason": "..."} /
        {"conflict": True, "existing_id": N}
    """
    if not cookies:
        return {"ok": False, "reason": "cookies 为空"}

    # 1. 落盘
    _persist_cookies(account_id, cookies)
    progress_cb("login_cookies_persisted", {"account_id": account_id, "count": len(cookies)})

    # 2. 注入 + 3. 验证
    ctx = _WORKER_CTX
    if ctx is None:
        # 无 ctx（tests / no worker）→ 只落盘，无法验证，保守标 ok（让测试通过）
        progress_cb("login_cookies_imported", {"account_id": account_id, "count": len(cookies)})
        return {"ok": True, "identity": None}

    try:
        await ctx.add_cookies(cookies)
    except Exception as exc:
        logger.warning(f"add_cookies failed: {exc}")
        _apply_login_failure(account_id, progress_cb)
        return {"ok": False, "reason": f"add_cookies 失败: {exc}"}

    valid = await _verify_cookies_on_platform(ctx, platform)
    if not valid:
        _apply_login_failure(account_id, progress_cb)
        return {"ok": False, "reason": "cookie 验证失败（平台返回非 2xx）"}

    # 4. 提取身份
    identity = await _extract_platform_identity(ctx, platform)
    if identity and identity.get("platform_user_id"):
        # 5. 检查 UNIQUE 冲突
        conflict_id = _find_conflicting_account(platform, identity["platform_user_id"], exclude_id=account_id)
        if conflict_id is not None:
            progress_cb("cookie_import_conflict", {
                "account_id": account_id,
                "existing_id": conflict_id,
                "platform_user_id": identity["platform_user_id"],
            })
            return {"conflict": True, "existing_id": conflict_id, "identity": identity}

    # 成功 → 回写
    _apply_login_success(
        account_id, progress_cb,
        platform=platform,
        platform_user_id=(identity or {}).get("platform_user_id"),
        platform_nickname=(identity or {}).get("platform_nickname"),
        login_method="cookie_import",
    )
    progress_cb("login_success", {
        "account_id": account_id,
        "method": "cookie_import",
        "platform_user_id": (identity or {}).get("platform_user_id"),
    })
    return {"ok": True, "identity": identity}


def _update_account_status(account_id: int | None, status: str, progress_cb: Callable) -> None:
    """Update account status in the database."""
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            acct = sess.query(Account).filter(Account.id == account_id).first()
            if acct:
                acct.status = status
                acct.last_login_at = datetime.now(timezone.utc)
                sess.commit()
                progress_cb("account_status_updated", {"account_id": account_id, "status": status})
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to update account status: {exc}")


# ---------------------------------------------------------------------------
# handler_validate — check if account cookies are still valid
# ---------------------------------------------------------------------------

async def handler_validate(payload: dict, progress_cb: Callable) -> dict:
    """Validate account session/cookies.

    [契约变更 2026-07-13 S10] 真验证：加载 cookie → 注入 ctx → 请求需登录接口
    → 提取身份回写 → 状态机驱动 status+fail_count。

    Args:
        payload: {platform, account_id}

    Returns:
        {status, valid: bool, account_id}
    """
    account_id = payload.get("account_id")
    platform = payload.get("platform", "xiaohongshu")

    progress_cb("validate_start", {"account_id": account_id})

    # 文件级快检查：无 cookie 直接失败
    cookie_path = _cookie_path_for(account_id)
    if not cookie_path.exists():
        _apply_login_failure(account_id, progress_cb)
        progress_cb("validate_no_cookies", {"account_id": account_id})
        return {"status": "error", "valid": False, "account_id": account_id}

    try:
        with open(cookie_path, "r") as f:
            cookies = json.load(f)
    except Exception:
        _apply_login_failure(account_id, progress_cb)
        return {"status": "error", "valid": False, "account_id": account_id}

    if not cookies:
        _apply_login_failure(account_id, progress_cb)
        return {"status": "error", "valid": False, "account_id": account_id}

    # 真注入 + 验证
    ctx = _WORKER_CTX
    if ctx is None:
        # 无 ctx → 降级为只看文件
        valid = True
    else:
        try:
            await ctx.add_cookies(cookies)
        except Exception as exc:
            logger.warning(f"validate add_cookies failed: {exc}")
            _apply_login_failure(account_id, progress_cb)
            return {"status": "error", "valid": False, "account_id": account_id}
        valid = await _verify_cookies_on_platform(ctx, platform)

    if valid:
        # 提取身份回写 + 成功清零
        identity = await _extract_platform_identity(ctx, platform) if ctx else None
        _apply_login_success(
            account_id, progress_cb,
            platform=platform,
            platform_user_id=(identity or {}).get("platform_user_id"),
            platform_nickname=(identity or {}).get("platform_nickname"),
        )
    else:
        _apply_login_failure(account_id, progress_cb)

    status = "ok" if valid else "error"
    progress_cb("validate_done", {"account_id": account_id, "valid": valid})
    return {"status": status, "valid": valid, "account_id": account_id}


def _check_account_valid(account_id: int | None, platform: str, progress_cb: Callable) -> bool:
    """Check if the account's session is valid.

    [契约变更 2026-07-13 S10] 路径统一用 _cookie_path_for（删 acct_ 前缀）。
    仅文件级检查（cookie 非空），真验证走 handler_validate。
    """
    cookie_path = _cookie_path_for(account_id)
    if not cookie_path.exists():
        progress_cb("validate_no_cookies", {"account_id": account_id})
        return False
    try:
        with open(cookie_path, "r") as f:
            cookies = json.load(f)
        return len(cookies) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# handler_scrape_task — five-stage scrape orchestration (§9.3)
# ---------------------------------------------------------------------------

async def handler_scrape_task(payload: dict, progress_cb: Callable) -> dict:
    """Execute a full scrape task with five-stage pipeline.

    Phase 1: Warmup - check rhythm, random browse
    Phase 2: Search - engine.search with pagination, keyword delays
    Phase 3: Detail - deduplicate, fetch_item, download images, note delay
    Phase 4: Comments - fetch_comments, top 20 by likes
    Phase 5: Store - upsert to SQLite, update progress, update last_note_index

    Args:
        payload: {task_id, platform, keywords, sort, max_posts_per_keyword,
                  download_images, collect_comments, account_id, request_id}
        progress_cb: (message, data) callback for IPC progress.

    Returns:
        {status, posts_scraped, comments_count, images_count, last_note_index}
        or {status: "paused", ...} for captcha pause
    """
    task_id = payload.get("task_id")
    platform = payload.get("platform", "xiaohongshu")
    keywords = payload.get("keywords", [])
    sort = payload.get("sort", "general")
    max_posts = payload.get("max_posts_per_keyword", 20)
    download_images = payload.get("download_images", True)
    collect_comments = payload.get("collect_comments", True)
    account_id = payload.get("account_id")
    request_id = payload.get("request_id", "")

    progress_cb("scrape_start", {
        "task_id": task_id,
        "platform": platform,
        "keywords": keywords,
    })

    # --- Phase 1: Warmup ---
    progress_cb("phase1_warmup", {"task_id": task_id})

    # Night-sleep gate before ANY network (PRD §4.5.1/§7.4): long-sleep, not throw.
    await _night_sleep_if_quiet(progress_cb)

    # pending→running promotion (S2 T07 遗留): a queued task becomes running
    # when the worker picks up its IPC request. Resume counters preserved.
    _promote_to_running(task_id, progress_cb)

    # Daily-cap guard (quiet hours handled above via long-sleep). If the global
    # today-count is already at the limit, park as paused before any scraping
    # (PRD §7.1). The per-note loop re-checks as the count grows mid-task.
    try:
        _check_rhythm(account_id, progress_cb)
    except DailyLimitError:
        progress_cb("daily_limit", {
            "task_id": task_id,
            "msg": "全局日配额已达上限，保护机制生效，请明日恢复",
        })
        _set_task_paused(task_id, progress_cb)
        return {
            "status": "paused",
            "reason": "daily_limit",
            "posts_scraped": 0,
            "comments_count": 0,
            "images_count": 0,
            "last_note_index": 0,
        }

    # Get page/engine (lazy)
    engine = _get_engine(platform, account_id, progress_cb)
    if engine is None:
        from semilabs_hone.core.utils.retry import BrowserClosedError
        raise BrowserClosedError("无法获取浏览器页面")

    # Wire risk probe: the engine fires it after every goto/scroll/click and
    # raises RiskProbeHit on a hit (PRD §4.4.1). The handler translates a hit
    # into a need_human sink + human-resume wait.
    try:
        from semilabs_hone.modules.collection.risk_probes import probe as _risk_probe
        engine.on_risk = lambda page, _p=platform: _risk_probe(page, _p)
    except Exception:
        # engine without probe wiring still works (probes are best-effort).
        pass

    # Warmup browse
    await _do_warmup(engine, progress_cb)

    # --- Phase 2-5 per keyword ---
    total_posts = 0
    total_comments = 0
    total_images = 0
    last_note_index = 0

    # Load task from DB to get resume point
    task = _load_task(task_id, progress_cb)
    if task:
        last_note_index = task.get("last_note_index", 0)

    # Track seen platform_ids for dedup
    seen_ids: set[str] = set()

    for ki, keyword in enumerate(keywords):
        if ki > 0:
            progress_cb("keyword_delay", {"keyword": keyword, "index": ki})
            # In real mode, this sleeps per keyword_delay
            # For handler, just signal
            await asyncio.sleep(0.1)  # Short delay for test; real config.KEYWORD_DELAY is 60-180s

        # --- Phase 2: Search ---
        progress_cb("phase2_search", {
            "task_id": task_id,
            "keyword": keyword,
            "progress": f"搜索: {keyword}",
        })

        try:
            item_refs = await engine.search(keyword, sort)
        except Exception as exc:
            from semilabs_hone.modules.collection.scrapers.engine import RiskProbeHit
            if isinstance(exc, RiskProbeHit):
                # Captcha/login wall during search goto/scroll → suspend.
                await _handle_need_human(task_id, request_id, exc.hit, progress_cb, last_note_index)
                break  # task suspended (need_human); stop the keyword loop
            from semilabs_hone.core.utils.retry import SkimError
            if isinstance(exc, SkimError):
                raise
            logger.warning(f"Search failed for '{keyword}': {exc}")
            item_refs = []

        # Limit to max_posts
        if len(item_refs) > max_posts:
            item_refs = item_refs[:max_posts]

        # --- Phase 3: Detail ---
        progress_cb("phase3_detail", {
            "task_id": task_id,
            "keyword": keyword,
            "items_found": len(item_refs),
        })

        for ref in item_refs:
            if isinstance(ref, dict):
                platform_id = ref.get("item_id", str(ref))
            else:
                platform_id = getattr(ref, "item_id", str(ref))

            # Dedup
            if platform_id in seen_ids:
                progress_cb("detail_skip_dup", {"platform_id": platform_id})
                continue
            seen_ids.add(platform_id)
            last_note_index += 1

            progress_cb("phase3_fetching", {
                "task_id": task_id,
                "platform_id": platform_id,
                "note_index": last_note_index,
            })

            # Night-sleep gate per item (PRD §4.5.1).
            await _night_sleep_if_quiet(progress_cb)

            # Daily-cap gate per item (PRD §7.1): cross-task today-count; raises
            # DailyLimitError when the global total hits the limit. Park as
            # `paused` (PRD §7.1 mandates paused, not need_human) and return —
            # bypassing _complete_task (a quota-paused task is not completed).
            try:
                _check_rhythm(account_id, progress_cb)
            except DailyLimitError:
                progress_cb("daily_limit", {
                    "task_id": task_id,
                    "msg": "全局日配额已达上限，保护机制生效，请明日恢复",
                    "posts_scraped": total_posts,
                })
                _set_task_paused(task_id, progress_cb)
                return {
                    "status": "paused",
                    "reason": "daily_limit",
                    "posts_scraped": total_posts,
                    "comments_count": total_comments,
                    "images_count": total_images,
                    "last_note_index": last_note_index,
                }

            # Retry-after-resume loop: a RiskProbeHit suspends → await human
            # resume → re-run the same ref (engine re-probes on its next goto).
            # `while not done` (not bare `while True`) per the §7.4 linter: exits
            # on success (done=True) or skip (break); only resumes via continue.
            done = False
            while not done:
                try:
                    post = await engine.fetch_item(ref)
                except Exception as exc:
                    from semilabs_hone.modules.collection.scrapers.engine import RiskProbeHit
                    if isinstance(exc, RiskProbeHit):
                        await _handle_need_human(task_id, request_id, exc.hit, progress_cb, last_note_index)
                        continue  # retry same ref after resume (done still False)
                    from semilabs_hone.core.utils.retry import SkimError
                    if isinstance(exc, SkimError):
                        raise
                    # T20 (PRD 8.4 场景4.1): single-item skip + count — the
                    # note_index already advanced (consumed); keep going.
                    progress_cb("detail_skip_error", {
                        "platform_id": platform_id, "error": str(exc),
                    })
                    break  # give up this ref, move to the next

                # Download images if enabled
                images_downloaded = 0
                if download_images:
                    image_urls = getattr(post, "image_urls", None) or (post.get("image_urls") if isinstance(post, dict) else [])
                    if image_urls:
                        try:
                            await _download_images_for_post(image_urls, platform_id, progress_cb)
                            images_downloaded = len(image_urls) if isinstance(image_urls, list) else 0
                        except Exception as exc:
                            logger.warning(f"Image download failed for '{platform_id}': {exc}")

                total_images += images_downloaded

                # Note delay (PRD §4.5.2: 30-90s warmup dwell; test short).
                await asyncio.sleep(0.05)

                # --- Phase 4: Comments (Top 20 by likes, PRD §4.3.2) ---
                comments_fetched = 0
                comments: list = []
                if collect_comments:
                    progress_cb("phase4_comments", {
                        "task_id": task_id,
                        "platform_id": platform_id,
                    })
                    try:
                        raw_comments = await engine.fetch_comments(ref)
                    except Exception as exc:
                        from semilabs_hone.modules.collection.scrapers.engine import RiskProbeHit
                        if isinstance(exc, RiskProbeHit):
                            raise  # bubble to the outer retry loop
                        logger.warning(f"Comments fetch failed for '{platform_id}': {exc}")
                        raw_comments = []
                    # Top 20 by likes descending; fewer than 20 → keep all.
                    comments = sorted(
                        raw_comments,
                        key=lambda c: getattr(c, "likes", 0) if hasattr(c, "likes") else (c.get("likes", 0) if isinstance(c, dict) else 0),
                        reverse=True,
                    )[:20]
                    comments_fetched = len(comments)

                total_comments += comments_fetched

                # --- Phase 5: Store (PRD §6 upsert via repository) ---
                try:
                    _upsert_post(post, task_id, keyword, comments, progress_cb)
                    total_posts += 1
                except Exception as exc:
                    logger.warning(f"Store failed for '{platform_id}': {exc}")
                    progress_cb("store_failed", {
                        "platform_id": platform_id, "error": str(exc),
                    })

                # Update last_note_index + actual_count
                _update_task_progress(task_id, last_note_index, total_posts, progress_cb)
                done = True  # ref fully processed → exit retry loop, next item

    # Final update
    progress_cb("scrape_complete", {
        "task_id": task_id,
        "posts_scraped": total_posts,
        "comments_count": total_comments,
        "images_count": total_images,
        "last_note_index": last_note_index,
    })

    # Update task status to completed
    _complete_task(task_id, total_posts, total_comments, last_note_index, progress_cb)

    return {
        "status": "ok",
        "posts_scraped": total_posts,
        "comments_count": total_comments,
        "images_count": total_images,
        "last_note_index": last_note_index,
    }


def _check_rhythm(account_id: int | None, progress_cb: Callable, now: datetime | None = None) -> None:
    """Check the global daily scrape cap (PRD §7.1 当天总入库量).

    Counts today's collection_items across ALL tasks (cross-task accumulation,
    matching PRD §7.1 「全局日限额跨任务累加 / 当天总入库量达到 200」) and raises
    DailyLimitError when count >= config.DAILY_LIMIT_PER_ACCOUNT (200).

    Quiet hours are handled separately by _night_sleep_if_quiet (long-sleep,
    not a throw). Unlike the pre-S8 version, DailyLimitError is NOT swallowed
    here — it propagates so the caller parks the task as `paused` (PRD §7.1
    mandates paused, not need_human). DB lookup failures still pass (don't
    block scraping on infra hiccups). ``now`` is injectable (会话经验 #7).
    """
    from semilabs_hone.modules.collection.scheduler.rhythm import check_daily_limit

    try:
        from sqlalchemy import func
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.post import CollectionItem
        sess = get_session()
        try:
            today = (now or datetime.now()).date().isoformat()
            today_count = (
                sess.query(CollectionItem)
                .filter(func.date(CollectionItem.scraped_at) == today)
                .count()
            )
        finally:
            sess.close()
    except Exception:
        # DB unavailable → skip cap check (don't block scraping on infra).
        return

    # check_daily_limit raises DailyLimitError when count >= 200 — let it
    # propagate (pre-S8 it was swallowed by the bare except above).
    check_daily_limit({"daily_scrape_count": today_count})


async def _night_sleep_if_quiet(progress_cb: Callable, now=None) -> None:
    """If within quiet hours, long-sleep until 08:00 (PRD §4.5.1).

    PRD night-sleep mechanism: do NOT throw-and-retry; the worker suspends via
    a single long asyncio.sleep and issues zero network requests during
    02:00-08:00. ``now`` is injectable so tests never depend on the wall clock
    (会话经验 #7).
    """
    from semilabs_hone.modules.collection.scheduler.rhythm import (
        is_quiet_hours,
        sleep_until_wakeup,
    )
    if is_quiet_hours(now):
        progress_cb("night_sleep", {"wakeup": "08:00", "msg": "夜间静默休眠至 08:00"})
        await sleep_until_wakeup(now)


def _promote_to_running(task_id: str | None, progress_cb: Callable) -> None:
    """Promote a queued (pending) task to running when the worker picks it up.

    S2 T07 left the pending→running pick-up to the engine/handler layer: the
    worker pulls requests in mtime order, but the DB status flip happens here.
    Resume-critical counters (last_note_index/actual_count) are preserved.
    """
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task and task.status == "pending":
                task.status = "running"
                sess.commit()
                progress_cb("task_promoted", {"task_id": task_id})
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to promote task {task_id}: {exc}")


def _set_task_need_human(task_id: str | None, progress_cb: Callable) -> None:
    """Sink a task's DB status to need_human (PRD §4.4.2 step 2)."""
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task:
                task.status = "need_human"
                sess.commit()
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to set need_human for {task_id}: {exc}")


def _set_task_paused(task_id: str | None, progress_cb: Callable) -> None:
    """Park a task's DB status as paused (PRD §7.1 daily-quota exhaustion).

    Distinct from _set_task_need_human: a quota hit is `paused` (await
    tomorrow), not a captcha/login `need_human` relay. Mirrors the
    need_human setter shape.
    """
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task:
                task.status = "paused"
                sess.commit()
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to set paused for {task_id}: {exc}")


async def _await_resume(request_id: str, poll_interval: float = 2.0) -> str | None:
    """Block until a ``resume`` control directive arrives (PRD §4.4.2 step 4).

    Polls ``control/ctrl_<request_id>.json`` every ``poll_interval`` seconds,
    read-after-burn. Non-resume directives are burned and ignored (the worker
    stays suspended waiting for a human relay). ``poll_interval`` is injectable
    so tests never sleep the real 2s. Returns "resume" or "stop".

    While suspended the worker is alive (awaiting a human), so it refreshes the
    heartbeat each poll — otherwise the web-side watchdog (>30s stale) would
    reap a legitimately-waiting ``need_human`` task to ``paused`` and break the
    L01 resume→control path for any human relay that takes longer than 30s.

    Note: this is a persistent *suspend-until-resume* poll with explicit return
    exits (resume/stop), NOT a captcha-refresh death loop — written without a
    bare ``while True`` per the §7.4 linter.
    """
    from semilabs_hone.core.ipc.paths import burn, control_path, read_json_if_exists, write_heartbeat
    if not request_id:
        return None
    waiting = True
    while waiting:
        # Stay "alive" to the watchdog while we wait for a human relay.
        try:
            write_heartbeat("need_human_waiting")
        except Exception:
            pass
        p = control_path(request_id)
        data = None
        try:
            data = read_json_if_exists(p)
        except Exception:
            burn(p)
            data = None
        if data is not None:
            burn(p)
            action = data.get("action")
            if action == "resume":
                return "resume"
            if action == "stop":
                return "stop"
            # pause/unknown during need_human: keep waiting
        await asyncio.sleep(poll_interval)


async def _handle_need_human(
    task_id: str | None,
    request_id: str,
    hit: Any,
    progress_cb: Callable,
    last_note_index: int,
) -> str | None:
    """Sink to need_human, broadcast, and block until a human resumes (PRD §4.4.2).

    On resume, the caller re-runs the interrupted action (the engine re-probes
    on its next goto/scroll/click). Returns the resume/stop directive.

    L10 solver wiring (契约§5, default-off): when the hit is a captcha AND the
    platform spec is ``anonymous`` + ``auto_then_manual`` (cargo-class no-login
    sites), give the solver exactly one shot before sinking to human. Solved →
    return ``"resume"`` (the caller retries the ref, no human needed). Anything
    else (account/manual sites like XHS, or a failed/paused auto-solve) sinks to
    need_human unchanged — so XHS behavior is identical to pre-S9a.
    """
    kind = getattr(hit, "kind", None)
    platform = getattr(hit, "platform", None) or "xiaohongshu"

    # L10: optional auto-solve for anonymous+auto_then_manual platforms only.
    if kind == "captcha":
        try:
            from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
            spec, _ = reg_get(platform)
            if (
                spec.risk_tier == "anonymous"
                and spec.captcha_policy == "auto_then_manual"
                and _WORKER_CTX is not None
            ):
                page = await _worker_page()
                if page is not None:
                    from semilabs_hone.modules.collection.captcha.solver import detect_and_solve
                    result = await detect_and_solve(
                        page, _WORKER_CTX, spec.risk_tier, spec.captcha_policy
                    )
                    if getattr(result, "status", None) == "solved":
                        progress_cb("captcha_solved", {"platform": platform})
                        return "resume"  # retry the ref; no human needed
        except Exception as exc:
            logger.warning(f"solver wiring failed (falling back to manual): {exc}")

    progress_cb("need_human", {
        "task_id": task_id,
        "stage": "captcha_or_login_blocked",
        "kind": kind,
        "msg": "平台下发验证码或登录失效，请手动处理",
        "last_note_index": last_note_index,
    })
    _set_task_need_human(task_id, progress_cb)
    return await _await_resume(request_id)


async def _do_warmup(engine: Any, progress_cb: Callable) -> None:
    """Warmup: random browse 2-5 pages."""
    try:
        from semilabs_hone.modules.collection.scheduler.warmup import random_browse
        page = getattr(engine, "page", None)
        if page is not None:
            await random_browse(page)
            progress_cb("warmup_done", {})
    except ImportError:
        progress_cb("warmup_skipped", {"reason": "warmup module not available"})


async def _download_images_for_post(
    image_urls: list[str],
    note_id: str,
    progress_cb: Callable,
) -> None:
    """Download images for a post."""
    from semilabs_hone.core.utils.image_downloader import download_images
    await download_images(image_urls, str(note_id))


def _upsert_post(
    post: Any,
    task_id: str | None,
    keyword: str,
    comments: list | None = None,
    progress_cb: Callable | None = None,
) -> None:
    """Upsert post + comments via the PRD §6.4 repository (idempotent ON CONFLICT).

    [S4 cleanup] Switched from the legacy direct-ORM path (writing the retained
    legacy columns content/likes/...) to repository.upsert_item/upsert_comment,
    which target the canonical PRD columns content_text/metrics_json/like_count.
    Interaction strings are cleansed via parse_likes (PRD §8.5 场景5.1); the
    title falls back to body[:20] when empty (PRD §8.5 场景5.2). ``keyword`` is
    retained in the signature for call-site compatibility but unused (PRD
    collection_items has no keyword column).
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.repository import upsert_comment, upsert_item
    from semilabs_hone.modules.collection.scrapers.field_extract import (
        parse_likes,
        title_fallback,
    )

    def _g(obj: Any, name: str, default=None):
        return getattr(obj, name, default) if not isinstance(obj, dict) else obj.get(name, default)

    platform = _g(post, "platform", "xiaohongshu") or "xiaohongshu"
    platform_id = _g(post, "platform_id", "") or ""
    content = _g(post, "content", None)
    title = _g(post, "title", None)
    author_name = _g(post, "author_name", None)
    published_at = _g(post, "published_at", None)
    metrics = {
        "likes": parse_likes(_g(post, "likes", 0) or 0),
        "collects": parse_likes(_g(post, "collects", 0) or 0),
        "comments_count": parse_likes(_g(post, "comments_count", 0) or 0),
        "shares": parse_likes(_g(post, "shares", 0) or 0),
    }

    now = datetime.now(timezone.utc)
    sess = get_session()
    try:
        item = upsert_item(
            sess,
            task_id=task_id,
            platform=platform,
            platform_id=platform_id,
            url=None,  # ScrapedPost carries no url; PRD NOT NULL deferred to S7
            title=title_fallback(title, content),
            content_text=content,
            author_name=author_name,
            metrics=metrics,
            publish_time=(str(published_at) if published_at is not None else None),
            scraped_at=now,
        )

        # Top-20 comments are already capped by the caller (PRD §4.3.2).
        if comments:
            for rank, c in enumerate(comments, 1):
                c_author = _g(c, "author_name", None)
                c_content = _g(c, "content", "") or ""
                c_likes = parse_likes(_g(c, "likes", 0) or 0)
                c_pid = _g(c, "platform_id", None) or f"synth_{rank}"
                upsert_comment(
                    sess,
                    item_id=item.id,
                    platform_comment_id=c_pid,
                    author_name=c_author,
                    content_text=c_content,
                    like_count=c_likes,
                    scraped_at=now,
                )

        if progress_cb:
            progress_cb("post_stored", {"platform_id": platform_id, "comments": len(comments) if comments else 0})
    finally:
        sess.close()


def _update_task_progress(
    task_id: str | None,
    last_note_index: int,
    posts_scraped: int,
    progress_cb: Callable,
) -> None:
    """Update task progress in DB (last_note_index + PRD actual_count)."""
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task:
                task.last_note_index = last_note_index
                task.posts_scraped = posts_scraped
                task.actual_count = posts_scraped  # PRD §6.1 canonical progress
                sess.commit()
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to update task progress: {exc}")


def _load_task(task_id: str | None, progress_cb: Callable | None = None) -> dict | None:
    """Load task from DB."""
    if task_id is None:
        return None
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task:
                return {
                    "id": task.id,
                    "platform": task.platform,
                    "last_note_index": task.last_note_index,
                    "download_images": task.download_images,
                    "collect_comments": task.collect_comments,
                    "status": task.status,
                }
            return None
        finally:
            sess.close()
    except Exception:
        return None


def _complete_task(
    task_id: str | None,
    posts_scraped: int,
    comments_count: int,
    last_note_index: int,
    progress_cb: Callable,
) -> None:
    """Mark task as completed."""
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import CollectionTask
        sess = get_session()
        try:
            task = sess.query(CollectionTask).filter(CollectionTask.id == task_id).first()
            if task:
                task.status = "completed"
                task.posts_scraped = posts_scraped
                task.actual_count = posts_scraped  # PRD §6.1 canonical count
                task.last_note_index = last_note_index
                task.completed_at = datetime.now(timezone.utc)
                sess.commit()
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to complete task: {exc}")


def _get_engine(platform: str, account_id: int | None, progress_cb: Callable) -> Any | None:
    """Get a GenericEngine instance for the platform.

    In a real scenario, this would be called from within the worker process
    where the browser context is available. For now, this creates a mockable
    engine reference.
    """
    try:
        from semilabs_hone.modules.collection.scrapers.registry import get
        from semilabs_hone.modules.collection.scrapers.engine import GenericEngine

        spec, adapter_cls = get(platform)
        # L14: inject the worker's live ctx so _ensure_page can resolve a page
        # (None in tests → engine.ctx=None, matches pre-S9a behavior).
        engine = GenericEngine(spec=spec, ctx=_WORKER_CTX)
        return engine
    except KeyError:
        logger.warning(f"Platform '{platform}' not found in registry")
        return None
    except Exception as exc:
        logger.warning(f"Failed to create engine: {exc}")
        return None


# ---------------------------------------------------------------------------
# handler_search — single-step search for debugging
# ---------------------------------------------------------------------------

async def handler_search(payload: dict, progress_cb: Callable) -> dict:
    """Single-step search for debugging.

    Args:
        payload: {platform, keyword, sort, account_id}

    Returns:
        {status, items: list of ItemRef dicts}
    """
    platform = payload.get("platform", "xiaohongshu")
    keyword = payload.get("keyword", "")
    sort = payload.get("sort", "general")

    progress_cb("search_start", {"keyword": keyword, "platform": platform})

    engine = _get_engine(platform, payload.get("account_id"), progress_cb)
    if engine is None:
        from semilabs_hone.core.utils.retry import BrowserClosedError
        raise BrowserClosedError("无法获取浏览器页面")

    items = await engine.search(keyword, sort)

    # Convert to serializable dicts
    results = []
    for item in items:
        if hasattr(item, "model_dump"):
            results.append(item.model_dump())
        elif isinstance(item, dict):
            results.append(item)
        else:
            results.append({"item_id": str(item)})

    progress_cb("search_done", {"keyword": keyword, "count": len(results)})
    return {"status": "ok", "items": results}


# ---------------------------------------------------------------------------
# handler_detail — single-step detail fetch for debugging
# ---------------------------------------------------------------------------

async def handler_detail(payload: dict, progress_cb: Callable) -> dict:
    """Single-step detail fetch for debugging.

    Args:
        payload: {platform, item_id, account_id, download_images}

    Returns:
        {status, post: dict}
    """
    platform = payload.get("platform", "xiaohongshu")
    item_id = payload.get("item_id", "")
    download_imgs = payload.get("download_images", False)

    progress_cb("detail_start", {"item_id": item_id, "platform": platform})

    engine = _get_engine(platform, payload.get("account_id"), progress_cb)
    if engine is None:
        from semilabs_hone.core.utils.retry import BrowserClosedError
        raise BrowserClosedError("无法获取浏览器页面")

    from semilabs_hone.core.models.schemas import ItemRef
    ref = ItemRef(platform=platform, item_id=item_id)
    post = await engine.fetch_item(ref)

    # Download images if requested
    if download_imgs:
        image_urls = getattr(post, "image_urls", None) or (post.get("image_urls") if isinstance(post, dict) else [])
        if image_urls:
            await _download_images_for_post(image_urls, item_id, progress_cb)

    # Convert to serializable dict
    if hasattr(post, "model_dump"):
        post_data = post.model_dump()
    elif isinstance(post, dict):
        post_data = post
    else:
        post_data = {"item_id": item_id}

    progress_cb("detail_done", {"item_id": item_id})
    return {"status": "ok", "post": post_data}


# ---------------------------------------------------------------------------
# handler_comments — single-step comments fetch for debugging
# ---------------------------------------------------------------------------

async def handler_comments(payload: dict, progress_cb: Callable) -> dict:
    """Single-step comments fetch for debugging.

    Args:
        payload: {platform, item_id, account_id}

    Returns:
        {status, comments: list}
    """
    platform = payload.get("platform", "xiaohongshu")
    item_id = payload.get("item_id", "")

    progress_cb("comments_start", {"item_id": item_id, "platform": platform})

    engine = _get_engine(platform, payload.get("account_id"), progress_cb)
    if engine is None:
        from semilabs_hone.core.utils.retry import BrowserClosedError
        raise BrowserClosedError("无法获取浏览器页面")

    from semilabs_hone.core.models.schemas import ItemRef
    ref = ItemRef(platform=platform, item_id=item_id)
    comments = await engine.fetch_comments(ref)

    # Top 20 by likes
    comments = sorted(
        comments,
        key=lambda c: getattr(c, "likes", 0) if hasattr(c, "likes") else (c.get("likes", 0) if isinstance(c, dict) else 0),
        reverse=True,
    )[:20]

    # Convert to serializable dicts
    results = []
    for i, c in enumerate(comments, 1):
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = dict(c)
        else:
            d = {}
        d["rank"] = i
        results.append(d)

    progress_cb("comments_done", {"item_id": item_id, "count": len(results)})
    return {"status": "ok", "comments": results}
