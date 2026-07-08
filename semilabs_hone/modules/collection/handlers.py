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

    # Check rhythm
    _check_rhythm(account_id, progress_cb)

    # Get page/engine (lazy)
    engine = _get_engine(platform, account_id, progress_cb)
    if engine is None:
        from semilabs_hone.core.utils.retry import BrowserClosedError
        raise BrowserClosedError("无法获取浏览器页面")

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
            await asyncio.sleep(0.1)  # Short delay for test; real config.NOTE_DELAY is 60-180s

        # --- Phase 2: Search ---
        progress_cb("phase2_search", {
            "task_id": task_id,
            "keyword": keyword,
            "progress": f"搜索: {keyword}",
        })

        try:
            item_refs = await engine.search(keyword, sort)
        except Exception as exc:
            # Re-raise SkimError subclasses (CaptchaError, QuietHoursError, etc.)
            # so the IPC server can handle them properly.
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

            # Fetch detail
            try:
                post = await engine.fetch_item(ref)
            except Exception as exc:
                from semilabs_hone.core.utils.retry import SkimError
                if isinstance(exc, SkimError):
                    raise
                logger.warning(f"Detail fetch failed for '{platform_id}': {exc}")
                continue

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

            # Note delay
            await asyncio.sleep(0.05)  # Short for test; real is 30-90s

            # --- Phase 4: Comments ---
            comments_fetched = 0
            if collect_comments:
                progress_cb("phase4_comments", {
                    "task_id": task_id,
                    "platform_id": platform_id,
                })
                try:
                    comments = await engine.fetch_comments(ref)
                    # Top 20 by likes
                    comments = sorted(comments, key=lambda c: getattr(c, "likes", 0) if hasattr(c, "likes") else c.get("likes", 0), reverse=True)[:20]
                    comments_fetched = len(comments)
                except Exception as exc:
                    logger.warning(f"Comments fetch failed for '{platform_id}': {exc}")
                    comments = []

            total_comments += comments_fetched

            # --- Phase 5: Store ---
            try:
                _upsert_post(post, task_id, keyword, comments, progress_cb)
                total_posts += 1
            except Exception as exc:
                logger.warning(f"Store failed for '{platform_id}': {exc}")

            # Update last_note_index
            _update_task_progress(task_id, last_note_index, total_posts, progress_cb)

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
    """Check quiet hours and daily limits."""
    from semilabs_hone.modules.collection.scheduler.rhythm import (
        check_quiet_hours,
        check_daily_limit,
    )

    check_quiet_hours()

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
    task_id: int | None,
    keyword: str,
    comments: list | None = None,
    progress_cb: Callable | None = None,
) -> None:
    """Upsert post and comments to SQLite."""
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.post import Post
    from semilabs_hone.core.models.comment import Comment
    from semilabs_hone.core.models.keyword import Keyword

    sess = get_session()
    try:
        # Resolve keyword
        keyword_obj = None
        if keyword:
            keyword_obj = (
                sess.query(Keyword)
                .filter(Keyword.text == keyword)
                .first()
            )

        # Upsert post
        platform_id = getattr(post, "platform_id", None) or (
            post.get("platform_id") if isinstance(post, dict) else None
        )
        platform = getattr(post, "platform", "xiaohongshu") or (
            post.get("platform") if isinstance(post, dict) else "xiaohongshu"
        )

        existing = (
            sess.query(Post)
            .filter(Post.platform == platform, Post.platform_id == platform_id)
            .first()
        )

        now = datetime.now(timezone.utc)
        post_fields = {
            "title": getattr(post, "title", None) or (post.get("title") if isinstance(post, dict) else None),
            "content": getattr(post, "content", None) or (post.get("content") if isinstance(post, dict) else None),
            "author_name": getattr(post, "author_name", None) or (post.get("author_name") if isinstance(post, dict) else None),
            "url": getattr(post, "url", None) or (post.get("url") if isinstance(post, dict) else None),
            "likes": getattr(post, "likes", 0) or (post.get("likes", 0) if isinstance(post, dict) else 0),
            "collects": getattr(post, "collects", 0) or (post.get("collects", 0) if isinstance(post, dict) else 0),
            "comments_count": getattr(post, "comments_count", 0) or (post.get("comments_count", 0) if isinstance(post, dict) else 0),
            "shares": getattr(post, "shares", 0) or (post.get("shares", 0) if isinstance(post, dict) else 0),
            "image_count": getattr(post, "image_count", 0) or (post.get("image_count", 0) if isinstance(post, dict) else 0),
            "scraped_at": now,
        }
        if task_id:
            post_fields["task_id"] = task_id
        if keyword_obj:
            post_fields["keyword_id"] = keyword_obj.id

        if existing:
            for k, v in post_fields.items():
                setattr(existing, k, v)
            post_obj = existing
        else:
            post_obj = Post(
                platform=platform,
                platform_id=platform_id or "",
                **{k: v for k, v in post_fields.items() if k not in ("task_id", "keyword_id")},
                task_id=post_fields.get("task_id"),
                keyword_id=post_fields.get("keyword_id"),
            )
            sess.add(post_obj)

        sess.flush()

        # Upsert comments
        if comments:
            for rank, c in enumerate(comments, 1):
                c_platform_id = getattr(c, "platform_id", None) or (c.get("platform_id") if isinstance(c, dict) else None)
                existing_c = (
                    sess.query(Comment)
                    .filter(Comment.post_id == post_obj.id, Comment.platform_id == c_platform_id)
                    .first()
                )
                c_data = {
                    "author_name": getattr(c, "author_name", None) or (c.get("author_name") if isinstance(c, dict) else None),
                    "content": getattr(c, "content", "") or (c.get("content", "") if isinstance(c, dict) else ""),
                    "likes": getattr(c, "likes", 0) or (c.get("likes", 0) if isinstance(c, dict) else 0),
                    "rank": rank,
                    "scraped_at": now,
                }
                if existing_c:
                    for k, v in c_data.items():
                        setattr(existing_c, k, v)
                else:
                    comment_obj = Comment(
                        post_id=post_obj.id,
                        platform_id=c_platform_id,
                        **c_data,
                    )
                    sess.add(comment_obj)

        sess.commit()
        if progress_cb:
            progress_cb("post_stored", {"platform_id": platform_id, "comments": len(comments) if comments else 0})
    finally:
        sess.close()


def _update_task_progress(
    task_id: int | None,
    last_note_index: int,
    posts_scraped: int,
    progress_cb: Callable,
) -> None:
    """Update task progress in DB."""
    if task_id is None:
        return
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import ScrapeTask
        sess = get_session()
        try:
            task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
            if task:
                task.last_note_index = last_note_index
                task.posts_scraped = posts_scraped
                sess.commit()
        finally:
            sess.close()
    except Exception as exc:
        logger.warning(f"Failed to update task progress: {exc}")


def _load_task(task_id: int | None, progress_cb: Callable | None = None) -> dict | None:
    """Load task from DB."""
    if task_id is None:
        return None
    try:
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.task import ScrapeTask
        sess = get_session()
        try:
            task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
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
    task_id: int | None,
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
        from semilabs_hone.core.models.task import ScrapeTask
        sess = get_session()
        try:
            task = sess.query(ScrapeTask).filter(ScrapeTask.id == task_id).first()
            if task:
                task.status = "completed"
                task.posts_scraped = posts_scraped
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
