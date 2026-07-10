"""DM-11 integration tests — handlers + routes (full mock).

Tests cover:
- build_registry returns all op keys
- handler_scrape_task five-stage mock → ok result + posts_scraped
- CaptchaError → paused/error result (exception propagation)
- Platform dropdown from registry (mock list_platforms)
- routes TestClient: POST /api/accounts, GET /tasks/new 200, GET /posts 200
- Contract test: build_registry callable

Naming: test_<method>_<scenario>_<expected>
Uses tmp_data_dir + db_session isolation.

pytest-asyncio mode=auto is set in pyproject.toml, so async tests
are automatically awaited by pytest.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_data_dir(monkeypatch):
    """Isolate data directory for tests."""
    td = Path(tempfile.mkdtemp())
    td.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SEMILABS_DATA_DIR", str(td))

    # Reload config so DB_URL picks up the new DATA_DIR
    import importlib
    import config
    importlib.reload(config)

    # Reset db engine before each test
    try:
        import semilabs_hone.core.models.db as db_mod
        db_mod.reset_engine()
    except Exception:
        pass

    # Reset registry cache
    try:
        import semilabs_hone.modules.collection.scrapers.registry as reg_mod
        reg_mod._registry_cache = None
    except Exception:
        pass

    # Remove existing DB if present
    db_file = td / "factory.db"
    if db_file.exists():
        db_file.unlink()

    yield td


@pytest.fixture
def db_session(tmp_data_dir):
    """Create tables and yield session, then close and drop tables."""
    from semilabs_hone.core.models.db import init_db, get_session, reset_engine, get_engine, Base
    init_db()
    sess = get_session()
    try:
        yield sess
    finally:
        sess.close()
        # Drop all tables for isolation
        engine = get_engine()
        Base.metadata.drop_all(engine)
        reset_engine()


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class TestContract:
    """Contract tests for DM-11 integration."""

    def test_dm11_integration_contract(self):
        """build_registry must be callable."""
        h = pytest.importorskip("semilabs_hone.modules.collection.handlers")
        assert callable(getattr(h, "build_registry", None))


# ---------------------------------------------------------------------------
# Build registry tests
# ---------------------------------------------------------------------------

class TestBuildRegistry:
    def test_build_registry_returns_all_ops(self):
        """build_registry returns dict with all required op keys."""
        from semilabs_hone.modules.collection.handlers import build_registry
        reg = build_registry()
        assert isinstance(reg, dict)
        for key in ["login", "validate", "scrape_task", "search", "detail", "comments"]:
            assert key in reg, f"Missing op key: {key}"
            assert callable(reg[key]), f"Handler for '{key}' not callable"


# ---------------------------------------------------------------------------
# Helper: create minimal test app
# ---------------------------------------------------------------------------

def _make_app():
    """Create minimal test app with collection routes."""
    from fastapi import FastAPI
    from semilabs_hone.core.models.db import init_db

    init_db()

    app = FastAPI()

    from semilabs_hone.core.ui.routes import dashboard as dash_mod
    from fastapi.templating import Jinja2Templates
    from pathlib import Path

    # Find templates dir
    repo_root = Path(__file__).resolve().parents[2]
    templates_dir = repo_root / "semilabs_hone" / "core" / "ui" / "templates"
    if not templates_dir.is_dir():
        # Fallback: current dir relative
        templates_dir = Path(__file__).resolve().parent.parent / "semilabs_hone" / "core" / "ui" / "templates"

    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.globals["get_modules"] = lambda: {}
    dash_mod.set_templates(templates)

    # Include routes
    from semilabs_hone.modules.collection.routes import accounts as acc_mod
    from semilabs_hone.modules.collection.routes import tasks as task_mod
    from semilabs_hone.modules.collection.routes import posts as post_mod
    from semilabs_hone.modules.collection.routes import export as exp_mod

    app.include_router(acc_mod.router)
    app.include_router(task_mod.router)
    app.include_router(post_mod.router)
    app.include_router(exp_mod.router)

    return app


# ---------------------------------------------------------------------------
# handler_scrape_task tests
# ---------------------------------------------------------------------------

async def _noop_async(*args, **kwargs):
    """Neutralizer for night-sleep/warmup helpers so tests never wall-clock."""
    return None


class TestHandlerScrapeTask:
    async def test_scrape_task_mock_engine_five_stages_ok(self, db_session, tmp_data_dir, monkeypatch):
        """handler_scrape_task with mocked engine returns ok + posts_scraped."""
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod

        # Create a simple ItemRef-like mock
        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        # Create proper async functions for mock engine
        async def mock_search(keyword, sort):
            return [FakeRef("ref1"), FakeRef("ref2")]

        async def mock_fetch_item(ref):
            from semilabs_hone.core.models.schemas import ScrapedPost
            return ScrapedPost(
                platform_id=ref.item_id,
                platform="xiaohongshu",
                title=f"Post {ref.item_id}",
                content="content",
                author_name="Author",
                url=f"https://test.com/{ref.item_id}",
                likes=10,
                collects=5,
                comments_count=3,
                shares=1,
                image_count=0,
            )

        async def mock_fetch_comments(ref):
            from semilabs_hone.core.models.schemas import ScrapedComment
            return [ScrapedComment(
                author_name="User1",
                content="Great!",
                likes=10,
                platform_id="c1",
            )]

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        # Create task in DB
        from semilabs_hone.core.models.task import CollectionTask
        task = CollectionTask(
            account_id=1,
            platform="xiaohongshu",
            status="running",
            max_posts_per_keyword=10,
        )
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        # Create account
        from semilabs_hone.core.models.account import Account
        acct = Account(platform="xiaohongshu", nickname="test")
        db_session.add(acct)
        db_session.commit()

        # Create keyword
        from semilabs_hone.core.models.keyword import Keyword
        kw = Keyword(text="test_kw", platform="xiaohongshu")
        db_session.add(kw)
        db_session.commit()

        progress_calls = []

        def capture_progress(message, data=None):
            progress_calls.append((message, data))

        # Patch _get_engine and _check_rhythm to bypass quiet hours
        original_get_engine = h_mod._get_engine
        original_check_rhythm = h_mod._check_rhythm
        original_night_sleep = h_mod._night_sleep_if_quiet
        h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
        h_mod._check_rhythm = lambda account_id, progress_cb: None
        h_mod._night_sleep_if_quiet = _noop_async  # never wall-clock long-sleep

        try:
            payload = {
                "task_id": task_id,
                "platform": "xiaohongshu",
                "keywords": ["test_kw"],
                "sort": "general",
                "max_posts_per_keyword": 10,
                "download_images": False,
                "collect_comments": True,
                "account_id": 1,
                "request_id": "test-req-1",
            }

            result = await handler_scrape_task(payload, capture_progress)

            assert result["status"] == "ok"
            assert result["posts_scraped"] >= 0
            assert "last_note_index" in result
        finally:
            h_mod._get_engine = original_get_engine
            h_mod._check_rhythm = original_check_rhythm
            h_mod._night_sleep_if_quiet = original_night_sleep

    async def test_scrape_task_captcha_error_raises(self, db_session, tmp_data_dir):
        """handler_scrape_task propagates CaptchaError for IPC server to handle."""
        from semilabs_hone.core.utils.retry import CaptchaError

        async def mock_search_raises(*args, **kwargs):
            raise CaptchaError("验证码检测")

        mock_engine = MagicMock()
        mock_engine.search = mock_search_raises
        mock_engine.page = None

        import semilabs_hone.modules.collection.handlers as h_mod
        original_get_engine = h_mod._get_engine
        original_check_rhythm = h_mod._check_rhythm
        original_night_sleep = h_mod._night_sleep_if_quiet
        h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
        h_mod._check_rhythm = lambda account_id, progress_cb: None
        h_mod._night_sleep_if_quiet = _noop_async

        progress_calls = []

        def capture_progress(message, data=None):
            progress_calls.append((message, data))

        payload = {
            "task_id": 1,
            "platform": "xiaohongshu",
            "keywords": ["test"],
            "sort": "general",
            "max_posts_per_keyword": 5,
            "download_images": False,
            "collect_comments": False,
            "account_id": 1,
            "request_id": "test-req-2",
        }

        try:
            from semilabs_hone.modules.collection.handlers import handler_scrape_task
            with pytest.raises(CaptchaError):
                await handler_scrape_task(payload, capture_progress)
        finally:
            h_mod._get_engine = original_get_engine
            h_mod._check_rhythm = original_check_rhythm
            h_mod._night_sleep_if_quiet = original_night_sleep


# ---------------------------------------------------------------------------
# helper: build a mock engine + patched handler env (no wall-clock, no real DB lookup)
# ---------------------------------------------------------------------------

def _patch_handler_env(h_mod, mock_engine):
    """Swap the engine/rhythm/night-sleep hooks for a mock; return originals."""
    orig = {
        "engine": h_mod._get_engine,
        "rhythm": h_mod._check_rhythm,
        "night": h_mod._night_sleep_if_quiet,
    }
    h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
    h_mod._check_rhythm = lambda account_id, progress_cb: None
    h_mod._night_sleep_if_quiet = _noop_async
    return orig


def _restore_handler_env(h_mod, orig):
    h_mod._get_engine = orig["engine"]
    h_mod._check_rhythm = orig["rhythm"]
    h_mod._night_sleep_if_quiet = orig["night"]


def _make_task(db_session, *, status="running", max_posts=10):
    from semilabs_hone.core.models.task import CollectionTask
    task = CollectionTask(account_id=1, platform="xiaohongshu",
                         status=status, max_posts_per_keyword=max_posts)
    db_session.add(task)
    db_session.commit()
    return task.id


# ---------------------------------------------------------------------------
# T20 — single-item skip + count (PRD §8.4 场景4.1)
# ---------------------------------------------------------------------------

class TestHandlerScrapeTaskSkipCount:
    async def test_timeout_one_item_skips_and_continues(self, db_session, tmp_data_dir):
        """A fetch_item TimeoutError on one ref skips it; the rest still store."""
        from semilabs_hone.core.models.schemas import ScrapedPost, ItemRef
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        call_count = {"n": 0}

        async def mock_fetch_item(ref):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TimeoutError("page.goto timed out")
            return ScrapedPost(platform_id=ref.item_id, title="ok",
                               content="c", author_name="A")

        async def mock_search(keyword, sort):
            return [FakeRef("bad"), FakeRef("good")]

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=10)
        orig = _patch_handler_env(h_mod, mock_engine)
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 10, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "req-skip",
            }, cap)
            assert result["status"] == "ok"
            assert result["posts_scraped"] == 1  # only the good ref stored
            # skip surfaced as a progress event (PRD 8.4 场景4.1)
            assert any(m == "detail_skip_error" for m, _ in progress)
        finally:
            _restore_handler_env(h_mod, orig)


# ---------------------------------------------------------------------------
# T23 — comments Top 20 (PRD §4.3.2)
# ---------------------------------------------------------------------------

class TestHandlerScrapeTaskCommentsTop20:
    async def test_comments_capped_to_top20_by_likes(self, db_session, tmp_data_dir):
        """25 comments → only top 20 stored, ordered by likes descending."""
        from semilabs_hone.core.models.schemas import ScrapedPost, ScrapedComment
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.comment import CollectionComment
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        async def mock_fetch_item(ref):
            return ScrapedPost(platform_id=ref.item_id, title="t", content="c")

        async def mock_fetch_comments(ref):
            # 25 comments, likes 1..25 (so top-20 keeps likes 25..6)
            return [ScrapedComment(author_name=f"u{i}", content=f"c{i}",
                                   likes=i, platform_id=f"cid{i}") for i in range(1, 26)]

        mock_engine = MagicMock()
        mock_engine.search = lambda kw, sort: _async_return([FakeRef("n1")])
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=5)
        orig = _patch_handler_env(h_mod, mock_engine)

        try:
            await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 5, "download_images": False,
                "collect_comments": True, "account_id": 1,
                "request_id": "req-top20",
            }, lambda m, d=None: None)

            sess = get_session()
            try:
                cmts = sess.query(CollectionComment).all()
                assert len(cmts) == 20
                # top comment must have the highest likes (25)
                likes = sorted(c.like_count for c in cmts)
                assert likes[-1] == 25
                assert likes[0] == 6  # 25..6 inclusive = 20 comments
            finally:
                sess.close()
        finally:
            _restore_handler_env(h_mod, orig)


async def _async_return(value):
    return value


# ---------------------------------------------------------------------------
# T24 — risk probe → need_human → resume (PRD §4.4.2/§4.4.3)
# ---------------------------------------------------------------------------

class TestHandlerScrapeTaskNeedHuman:
    async def test_probe_hit_then_resume_retries_same_ref(self, db_session, tmp_data_dir):
        """fetch_item raises RiskProbeHit once → need_human + await_resume → retry succeeds."""
        from semilabs_hone.core.models.schemas import ScrapedPost
        from semilabs_hone.core.utils.retry import SkimError  # noqa
        from semilabs_hone.modules.collection.scrapers.engine import RiskProbeHit
        from semilabs_hone.modules.collection.risk_probes import ProbeHit
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod

        class FakeRef:
            def __init__(self, item_id):
                self.item_id = item_id

        attempts = {"n": 0}

        async def mock_fetch_item(ref):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RiskProbeHit(ProbeHit(kind="captcha", platform="xiaohongshu"))
            return ScrapedPost(platform_id=ref.item_id, title="t", content="c")

        async def mock_search(keyword, sort):
            return [FakeRef("n1")]

        async def mock_fetch_comments(ref):
            return []

        mock_engine = MagicMock()
        mock_engine.search = mock_search
        mock_engine.fetch_item = mock_fetch_item
        mock_engine.fetch_comments = mock_fetch_comments
        mock_engine.page = None

        task_id = _make_task(db_session, max_posts=5)
        orig = _patch_handler_env(h_mod, mock_engine)
        # Resume immediately (no real 2s poll / human).
        orig_await = h_mod._await_resume
        h_mod._await_resume = _noop_async

        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            result = await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 5, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "req-needhuman",
            }, cap)
            assert result["status"] == "ok"
            assert result["posts_scraped"] == 1
            # need_human surfaced + ref retried after resume
            assert any(m == "need_human" for m, _ in progress)
            assert attempts["n"] == 2
        finally:
            h_mod._await_resume = orig_await
            _restore_handler_env(h_mod, orig)


# ---------------------------------------------------------------------------
# T25 — night-sleep long-sleep, not throw (PRD §4.5.1)
# ---------------------------------------------------------------------------

class TestHandlerScrapeTaskNightSleep:
    async def test_quiet_hours_triggers_night_sleep_before_network(self, db_session, tmp_data_dir, monkeypatch):
        """In quiet hours the handler long-sleeps (does NOT raise QuietHoursError)."""
        from semilabs_hone.modules.collection.handlers import handler_scrape_task
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.modules.collection.scheduler import rhythm as rhythm_mod
        from datetime import datetime

        slept = []

        async def fake_sleep_until_wakeup(now=None):
            slept.append(now)
            return 0.0

        # Real _night_sleep_if_quiet, but with a quiet `now` + patched sleep.
        orig_night = h_mod._night_sleep_if_quiet
        orig_rhythm_sleep = rhythm_mod.sleep_until_wakeup
        # Force quiet: 03:00
        monkeypatch.setattr(rhythm_mod, "is_quiet_hours", lambda now=None: True)
        rhythm_mod.sleep_until_wakeup = fake_sleep_until_wakeup

        # engine never reached (night-sleep gates before search); stub minimal.
        mock_engine = MagicMock()
        mock_engine.search = lambda kw, sort: _async_return([])
        mock_engine.fetch_item = lambda ref: _async_return(None)
        mock_engine.fetch_comments = lambda ref: _async_return([])
        mock_engine.page = None
        orig = _patch_handler_env(h_mod, mock_engine)
        # Restore the REAL night-sleep helper (patch env above no-op'd it).
        h_mod._night_sleep_if_quiet = orig_night

        task_id = _make_task(db_session, max_posts=5)
        progress = []

        def cap(m, d=None):
            progress.append((m, d))

        try:
            await handler_scrape_task({
                "task_id": task_id, "platform": "xiaohongshu",
                "keywords": ["kw"], "sort": "general",
                "max_posts_per_keyword": 5, "download_images": False,
                "collect_comments": False, "account_id": 1,
                "request_id": "req-night",
            }, cap)
            assert any(m == "night_sleep" for m, _ in progress)
            assert slept, "sleep_until_wakeup must be invoked in quiet hours"
        finally:
            rhythm_mod.sleep_until_wakeup = orig_rhythm_sleep
            _restore_handler_env(h_mod, {**orig, "night": orig_night})


# ---------------------------------------------------------------------------
# _await_resume — control polling (PRD §4.4.2 step 4)
# ---------------------------------------------------------------------------

class TestAwaitResume:
    async def test_resume_control_file_returns_resume(self, tmp_data_dir, monkeypatch):
        """A pre-written resume control directive is read-and-burned → returns 'resume'."""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.ipc.paths import atomic_write_json, control_path

        rid = "resume-rid"
        atomic_write_json(control_path(rid), {"action": "resume"})

        result = await h_mod._await_resume(rid, poll_interval=0.01)
        assert result == "resume"
        # read-after-burn: the control file is gone
        assert not control_path(rid).exists()

    async def test_no_directive_keeps_polling_until_resume(self, tmp_data_dir, monkeypatch):
        """No file → polls; once resume appears → returns."""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.ipc.paths import atomic_write_json, control_path

        rid = "poll-rid"
        # Write the resume file after a short delay (simulate human relay).
        import asyncio

        async def write_later():
            await asyncio.sleep(0.05)
            atomic_write_json(control_path(rid), {"action": "resume"})

        task = asyncio.create_task(write_later())
        result = await h_mod._await_resume(rid, poll_interval=0.01)
        await task
        assert result == "resume"


# ---------------------------------------------------------------------------
# handler_login tests
# ---------------------------------------------------------------------------

class TestHandlerLogin:
    async def test_handler_login_qrcode_returns_ok(self, tmp_data_dir, monkeypatch):
        """handler_login with method='qrcode' returns ok with qr_path."""
        import semilabs_hone.modules.collection.handlers as h_mod

        # Patch DB operations
        original_recovery = h_mod._try_cookie_recovery
        original_qr = h_mod._do_qr_login
        original_update = h_mod._update_account_status

        h_mod._try_cookie_recovery = lambda *a: False
        h_mod._do_qr_login = lambda *a: {"qr_path": "/test/qr.png"}
        h_mod._update_account_status = lambda *a: None

        try:
            result = await h_mod.handler_login(
                {"platform": "xiaohongshu", "account_id": 1, "method": "qrcode"},
                lambda m, d=None: None,
            )
            assert result["status"] == "ok"
            assert result["login_method"] == "qrcode"
            assert "qr_path" in result
        finally:
            h_mod._try_cookie_recovery = original_recovery
            h_mod._do_qr_login = original_qr
            h_mod._update_account_status = original_update


# ---------------------------------------------------------------------------
# handler_validate tests
# ---------------------------------------------------------------------------

class TestHandlerValidate:
    async def test_handler_validate_returns_dict(self, tmp_data_dir):
        """handler_validate returns dict with valid field."""
        from semilabs_hone.modules.collection.handlers import handler_validate

        result = await handler_validate(
            {"platform": "xiaohongshu", "account_id": 1},
            lambda m, d=None: None,
        )
        assert isinstance(result, dict)
        assert "valid" in result
        assert "status" in result


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestRoutesAccounts:
    def test_post_api_accounts_creates_account(self, db_session, tmp_data_dir):
        """POST /api/accounts creates account and redirects."""
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/accounts",
            data={"platform": "xiaohongshu", "nickname": "test_account"},
            follow_redirects=False,
        )
        assert resp.status_code in (200, 303, 307)

    def test_get_tasks_new_200(self, db_session, tmp_data_dir):
        """GET /tasks/new returns 200."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/tasks/new")
        assert resp.status_code == 200

    def test_get_posts_200(self, db_session, tmp_data_dir):
        """GET /posts returns 200."""
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/posts")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Platform registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_list_platforms_returns_list(self, tmp_data_dir):
        """list_platforms returns a list of platform names."""
        from semilabs_hone.modules.collection.scrapers.registry import list_platforms
        platforms = list_platforms()
        assert isinstance(platforms, list)
