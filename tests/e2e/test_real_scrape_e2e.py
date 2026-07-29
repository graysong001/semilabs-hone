"""End-to-end: real Chrome over CDP against a real local site.

Nothing here is mocked. A real Chrome is launched the way the product
launches it (only ``--remote-debugging-port`` and ``--user-data-dir``),
attached over CDP, handed to the real collection handlers, and the results
are asserted in the real SQLite database and read back through the real
HTTP surface.

Skipped only when Chrome is missing (CI); on a dev Mac it runs.
Run just this file with:
    python3 -m pytest tests/e2e -q
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

import config

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not Path(config.CHROME_BIN).exists(),
        reason=f"real Chrome not found at {config.CHROME_BIN}",
    ),
]

CDP_READY_TIMEOUT = 30.0


class BrowserSession:
    """Real Chrome + CDP context, torn down cleanly."""

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self.proc = None
        self.browser = None
        self.ctx = None

    async def __aenter__(self) -> "BrowserSession":
        from semilabs_hone.modules.collection.browser.cdp import (
            attach,
            find_free_port,
            launch_real_chrome,
        )

        port = find_free_port()
        self.proc = launch_real_chrome(str(self.profile_dir), port)
        # attach() owns the wait-for-CDP-port retry, same as in production.
        self.browser, self.ctx = await attach(port, timeout=CDP_READY_TIMEOUT)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()


@pytest.fixture
def real_browser(tmp_data_dir):
    """A real Chrome bound to a throwaway profile."""
    from semilabs_hone.modules.collection.browser.profile import ensure_profile

    return BrowserSession(ensure_profile(9001))


@pytest.fixture(autouse=True)
def e2e_runtime_config(monkeypatch):
    """Config for an unattended run: no quiet window, no external warmup.

    Also undoes the unit-test QR shortcut: conftest's autouse fixture caps
    QR_LOGIN_TIMEOUT at 0.05s, but the local site only redirects after
    ~800ms — the real QR poll needs a real window here.
    """
    from semilabs_hone.modules.collection import handlers

    monkeypatch.setattr(config, "QUIET_HOURS", None, raising=False)
    monkeypatch.setattr(config, "WARMUP_PAGES", None, raising=False)
    monkeypatch.setattr(handlers, "QR_LOGIN_TIMEOUT", 30.0, raising=False)
    monkeypatch.setattr(handlers, "QR_POLL_INTERVAL", 0.3, raising=False)


def _account(db_session, platform: str, *, status="active"):
    from semilabs_hone.core.models.account import Account
    from semilabs_hone.modules.collection.anti_detect.fingerprint import assign_fingerprint

    fp = assign_fingerprint()
    acct = Account(
        platform=platform, nickname="e2e", status=status,
        viewport_w=fp.viewport["width"], viewport_h=fp.viewport["height"],
        color_scheme=fp.color_scheme, timezone=fp.timezone, locale=fp.locale,
    )
    db_session.add(acct)
    db_session.commit()
    return acct


def _task(db_session, account_id: int, platform: str, **overrides):
    from semilabs_hone.core.models.task import CollectionTask

    fields = {
        "platform": platform,
        "status": "running",
        "expected_count": 5,
    }
    fields.update(overrides)
    task = CollectionTask(**fields)
    db_session.add(task)
    db_session.commit()
    return task.id


async def _inject(ctx, account):
    """Do what worker_main does after attach: resources + stealth noise."""
    from semilabs_hone.modules.collection import handlers
    from semilabs_hone.modules.collection.anti_detect.stealth import inject_noise

    await inject_noise(ctx)
    handlers.set_worker_resources(ctx=ctx, account=account)
    return handlers


# ---------------------------------------------------------------------------
# The SOP happy path: login -> validate -> scrape -> browse -> export
# ---------------------------------------------------------------------------

class TestRealScrapeJourney:
    async def test_scrape_task_stores_real_posts_and_comments(
        self, db_session, tmp_data_dir, localtest_platform, real_browser
    ):
        """USER_SOP S6/G18: the whole worker chain, for real, end to end."""
        from semilabs_hone.core.models.comment import CollectionComment
        from semilabs_hone.core.models.post import CollectionItem
        from semilabs_hone.core.models.repository import unpack_metrics

        account = _account(db_session, localtest_platform)
        task_id = _task(db_session, account.id, localtest_platform)

        progress: list[tuple[str, dict]] = []

        async with real_browser as session:
            handlers = await _inject(session.ctx, account)
            result = await handlers.handler_scrape_task(
                {
                    "task_id": task_id,
                    "platform": localtest_platform,
                    "keywords": ["咖啡"],
                    "sort": "general",
                    "expected_count": 5,
                    "download_images": False,
                    "collect_comments": True,
                    "account_id": account.id,
                    "request_id": "e2e-scrape",
                },
                lambda message, data=None: progress.append((message, data or {})),
            )

        assert result["status"] == "ok"
        assert result["posts_scraped"] == 2, progress
        assert result["comments_count"] == 3

        posts = db_session.query(CollectionItem).filter(CollectionItem.task_id == task_id).all()
        by_id = {p.platform_id: p for p in posts}
        assert set(by_id) == {"note-1", "note-2"}

        first = by_id["note-1"]
        assert first.platform == localtest_platform
        assert first.title == "手冲咖啡入门"
        assert "正文内容" in (first.content_text or "")
        assert first.author_name == "咖啡爱好者"
        metrics = unpack_metrics(first.metrics_json)
        assert metrics["likes"] == 128 and metrics["collects"] == 12
        assert first.publish_time is not None

        comments = (
            db_session.query(CollectionComment)
            .filter(CollectionComment.item_id == first.id)
            .order_by(CollectionComment.like_count.desc())
            .all()
        )
        assert [c.content_text for c in comments] == ["水温多少？", "学到了！"]
        assert [c.like_count for c in comments] == [21, 9]

        percents = [d["percent"] for _, d in progress if "percent" in d]
        assert percents and percents[-1] == 100

        db_session.expire_all()
        db_session.refresh(account)
        assert account.daily_scrape_count == 2

    async def test_task_completes_and_reads_back_over_http(
        self, db_session, tmp_data_dir, localtest_platform, real_browser
    ):
        """S7/S8: scraped rows are browsable and exportable through the app."""
        from fastapi.testclient import TestClient

        from semilabs_hone.core.models.db import init_db
        from semilabs_hone.core.models.post import CollectionItem
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.ui.app import create_app

        account = _account(db_session, localtest_platform)
        task_id = _task(db_session, account.id, localtest_platform)

        async with real_browser as session:
            handlers = await _inject(session.ctx, account)
            await handlers.handler_scrape_task(
                {
                    "task_id": task_id,
                    "platform": localtest_platform,
                    "keywords": ["手冲"],
                    "sort": "general",
                    "expected_count": 5,
                    "download_images": False,
                    "collect_comments": False,
                    "account_id": account.id,
                    "request_id": "e2e-http",
                },
                lambda message, data=None: None,
            )

        db_session.expire_all()
        assert db_session.query(CollectionTask).filter(
            CollectionTask.id == task_id
        ).one().status == "completed"

        init_db()
        with TestClient(create_app()) as client:
            listing = client.get("/posts")
            assert listing.status_code == 200
            assert "V60 手冲参数" in listing.text

            post = db_session.query(CollectionItem).filter(
                CollectionItem.task_id == task_id
            ).first()
            detail = client.get(f"/posts/{post.id}")
            assert detail.status_code == 200

            csv_resp = client.get(f"/api/export?task_id={task_id}")
            assert csv_resp.status_code == 200
            assert "V60" in csv_resp.text

            # This task never went through IPC (handler called in-process):
            # no request_id, no progress file — the snapshot endpoint answers
            # its 404 degradation (G6). The completed status was already
            # asserted straight from the DB above; the full-stack E2E covers
            # the populated-progress path.
            progress_resp = client.get(f"/api/tasks/{task_id}/progress")
            assert progress_resp.status_code == 404
            assert progress_resp.json()["ok"] is False

    async def test_resume_skips_already_scraped_notes(
        self, db_session, tmp_data_dir, localtest_platform, real_browser
    ):
        """S9: a resumed run re-searches but does not re-fetch stored notes."""
        from semilabs_hone.core.models.post import CollectionItem

        account = _account(db_session, localtest_platform)
        task_id = _task(db_session, account.id, localtest_platform)
        payload = {
            "task_id": task_id,
            "platform": localtest_platform,
            "keywords": ["咖啡"],
            "sort": "general",
            "expected_count": 5,
            "download_images": False,
            "collect_comments": False,
            "account_id": account.id,
            "request_id": "e2e-resume",
        }

        async with real_browser as session:
            handlers = await _inject(session.ctx, account)
            first = await handlers.handler_scrape_task(payload, lambda m, d=None: None)

            second_progress: list[tuple[str, dict]] = []
            second = await handlers.handler_scrape_task(
                {**payload, "request_id": "e2e-resume-2", "resume": True},
                lambda message, data=None: second_progress.append((message, data or {})),
            )

        assert first["posts_scraped"] == 2
        assert second["posts_scraped"] == 0  # everything was already stored
        skipped = [d["platform_id"] for m, d in second_progress if m == "detail_skip_dup"]
        assert sorted(skipped) == ["note-1", "note-2"]
        assert db_session.query(CollectionItem).filter(CollectionItem.task_id == task_id).count() == 2


# ---------------------------------------------------------------------------
# Login / session, for real
# ---------------------------------------------------------------------------

class TestRealLogin:
    async def test_qr_login_reports_screenshot_then_succeeds(
        self, db_session, tmp_data_dir, localtest_platform, real_browser
    ):
        """S3: the QR tier navigates, screenshots, and detects the redirect."""
        from semilabs_hone.core.models.account import Account

        account = _account(db_session, localtest_platform, status="inactive")
        progress: list[tuple[str, dict]] = []

        async with real_browser as session:
            handlers = await _inject(session.ctx, account)
            handlers.QR_POLL_INTERVAL = 0.3
            result = await handlers.handler_login(
                {
                    "account_id": account.id,
                    "platform": localtest_platform,
                    "method": "qrcode",
                    "request_id": "e2e-login",
                },
                lambda message, data=None: progress.append((message, data or {})),
            )

        assert result["status"] == "ok"
        assert result["login_method"] == "qrcode"

        qr_events = [d for m, d in progress if m == "qr_ready"]
        assert qr_events and qr_events[0]["screenshot_b64"]

        db_session.expire_all()
        assert db_session.query(Account).filter(
            Account.id == account.id
        ).one().status == "active"

    async def test_validate_reports_a_live_session(
        self, db_session, tmp_data_dir, localtest_platform, real_browser
    ):
        """S4: session validation is a boolean answer, never an error."""
        account = _account(db_session, localtest_platform)

        async with real_browser as session:
            # A real cookie in the real profile, then the real check.
            await session.ctx.add_cookies([{
                "name": "sid", "value": "e2e",
                "domain": "127.0.0.1", "path": "/",
            }])
            handlers = await _inject(session.ctx, account)
            result = await handlers.handler_validate(
                {
                    "account_id": account.id,
                    "platform": localtest_platform,
                    "request_id": "e2e-validate",
                },
                lambda message, data=None: None,
            )

        assert result["status"] == "ok"
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# Anti-detection, verified in the real browser
# ---------------------------------------------------------------------------

class TestRealAntiDetection:
    async def test_webdriver_is_undefined_and_fingerprint_is_applied(
        self, db_session, tmp_data_dir, localtest_platform, real_browser, local_site
    ):
        """Layers 1-3: clean Chrome, no automation traces, CDP emulation on."""
        from semilabs_hone.modules.collection.anti_detect.fingerprint import (
            apply_to_page,
            load_fingerprint,
        )

        account = _account(db_session, localtest_platform)
        account.timezone = "Asia/Tokyo"
        account.locale = "ja-JP"
        db_session.commit()

        async with real_browser as session:
            await _inject(session.ctx, account)
            page = await session.ctx.new_page()
            await page.goto(local_site.base_url, wait_until="domcontentloaded")

            assert await page.evaluate("navigator.webdriver") in (None, False)
            assert await page.evaluate("typeof window.chrome") == "object"
            # Canvas noise is injected but the WebGL vendor stays authentic.
            vendor = await page.evaluate(
                "(() => { const c = document.createElement('canvas');"
                " const gl = c.getContext('webgl');"
                " return gl ? gl.getParameter(gl.VENDOR) : 'none'; })()"
            )
            assert vendor and vendor != "none"

            await apply_to_page(page, load_fingerprint(account))
            timezone = await page.evaluate(
                "Intl.DateTimeFormat().resolvedOptions().timeZone"
            )
            assert timezone == "Asia/Tokyo"
            await page.close()

    async def test_warmup_browses_real_pages(
        self, db_session, tmp_data_dir, local_site, real_browser, monkeypatch
    ):
        """Layer 4: warmup really navigates (against the local site, not the web)."""
        from semilabs_hone.modules.collection.scheduler import warmup

        monkeypatch.setattr(config, "WARMUP_PAGES", (2, 2), raising=False)

        async with real_browser as session:
            page = await session.ctx.new_page()
            await warmup.random_browse(page, urls=[
                f"{local_site.base_url}/",
                f"{local_site.base_url}/search?keyword=咖啡&sort=general",
            ])
            assert local_site.base_url in page.url
            await page.close()
