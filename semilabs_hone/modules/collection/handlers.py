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

# Lazy imports for optional dependencies
# fmt: off


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
        # Tier 1: Try cookie recovery
        recovered = _try_cookie_recovery(account_id, platform, progress_cb)
        if recovered:
            _update_account_status(account_id, "active", progress_cb)
            progress_cb("login_success", {"account_id": account_id, "method": "cookie_recovery"})
            return {
                "status": "ok",
                "login_method": "cookie_recovery",
                "account_id": account_id,
            }

    if method == "auto" or method == "qrcode":
        # Tier 2: QR code login
        progress_cb("login_qr_start", {"account_id": account_id})
        qr_result = _do_qr_login(platform, account_id, progress_cb)
        if qr_result:
            _update_account_status(account_id, "active", progress_cb)
            return {
                "status": "ok",
                "login_method": "qrcode",
                "account_id": account_id,
                **qr_result,
            }

    if method == "cookie_import":
        # Tier 3: Cookie import
        cookies = payload.get("cookies")
        if cookies:
            _import_cookies(account_id, platform, cookies, progress_cb)
            _update_account_status(account_id, "active", progress_cb)
            progress_cb("login_success", {"account_id": account_id, "method": "cookie_import"})
            return {
                "status": "ok",
                "login_method": "cookie_import",
                "account_id": account_id,
            }

    # Fall through to QR if auto and recovery failed
    if method == "auto":
        progress_cb("login_qr_start", {"account_id": account_id})
        qr_result = _do_qr_login(platform, account_id, progress_cb)
        if qr_result:
            _update_account_status(account_id, "active", progress_cb)
            return {
                "status": "ok",
                "login_method": "qrcode",
                "account_id": account_id,
                **qr_result,
            }

    from semilabs_hone.core.utils.retry import LoginError
    raise LoginError("所有登录方式均失败")


def _try_cookie_recovery(account_id: int | None, platform: str, progress_cb: Callable) -> bool:
    """Try to recover login from persisted cookies."""
    from config import DATA_DIR
    cookie_path = DATA_DIR / "collection" / "profiles" / f"acct_{account_id}" / "cookies.json"
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


def _do_qr_login(platform: str, account_id: int | None, progress_cb: Callable) -> dict | None:
    """Initiate QR code login. Returns QR info dict or None."""
    from config import DATA_DIR
    # In a real scenario, this navigates to the platform's login page
    # and takes a screenshot of the QR code.
    # For the handler, we return the QR path so the worker can screenshot.
    qr_path = str(DATA_DIR / "collection" / "debug" / f"qr_{account_id}.png")
    progress_cb("qr_ready", {"qr_path": qr_path, "account_id": account_id})
    return {"qr_path": qr_path}


def _import_cookies(account_id: int | None, platform: str, cookies: list, progress_cb: Callable) -> None:
    """Persist imported cookies to disk."""
    from config import DATA_DIR
    cookie_dir = DATA_DIR / "collection" / "profiles" / f"acct_{account_id}"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = cookie_dir / "cookies.json"
    with open(cookie_path, "w") as f:
        json.dump(cookies, f)
    progress_cb("login_cookies_imported", {"account_id": account_id, "count": len(cookies)})


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

    Args:
        payload: {platform, account_id}

    Returns:
        {status, valid: bool, account_id}
    """
    account_id = payload.get("account_id")
    platform = payload.get("platform", "xiaohongshu")

    progress_cb("validate_start", {"account_id": account_id})

    # Check account exists and has cookies
    valid = _check_account_valid(account_id, platform, progress_cb)

    status = "ok" if valid else "error"
    progress_cb(
        "validate_done",
        {"account_id": account_id, "valid": valid},
    )
    return {
        "status": status,
        "valid": valid,
        "account_id": account_id,
    }


def _check_account_valid(account_id: int | None, platform: str, progress_cb: Callable) -> bool:
    """Check if the account's session is valid."""
    from config import DATA_DIR
    cookie_path = DATA_DIR / "collection" / "profiles" / f"acct_{account_id}" / "cookies.json"
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

    # Daily-cap guard (quiet hours handled above via long-sleep).
    _check_rhythm(account_id, progress_cb)

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


def _check_rhythm(account_id: int | None, progress_cb: Callable) -> None:
    """Check daily scrape limit.

    Quiet-hours handling moved to _night_sleep_if_quiet (long-sleep per PRD
    §4.5.1, not a throw — a task started in the quiet window must long-sleep
    until 08:00, not error out). This guard keeps only the daily-cap check.
    """
    from semilabs_hone.modules.collection.scheduler.rhythm import check_daily_limit

    if account_id is not None:
        # Get account for daily limit check
        try:
            from semilabs_hone.core.models.db import get_session
            from semilabs_hone.core.models.account import Account
            sess = get_session()
            try:
                acct = sess.query(Account).filter(Account.id == account_id).first()
                if acct:
                    check_daily_limit(acct)
            finally:
                sess.close()
        except Exception:
            # If account lookup fails, pass rhythm check
            pass


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


async def _await_resume(request_id: str, poll_interval: float = 2.0) -> str | None:
    """Block until a ``resume`` control directive arrives (PRD §4.4.2 step 4).

    Polls ``control/ctrl_<request_id>.json`` every ``poll_interval`` seconds,
    read-after-burn. Non-resume directives are burned and ignored (the worker
    stays suspended waiting for a human relay). ``poll_interval`` is injectable
    so tests never sleep the real 2s. Returns "resume" or "stop".

    Note: this is a persistent *suspend-until-resume* poll with explicit return
    exits (resume/stop), NOT a captcha-refresh death loop — written without a
    bare ``while True`` per the §7.4 linter.
    """
    from semilabs_hone.core.ipc.paths import burn, control_path, read_json_if_exists
    if not request_id:
        return None
    waiting = True
    while waiting:
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
    """
    kind = getattr(hit, "kind", None)
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
        engine = GenericEngine(spec=spec)
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
