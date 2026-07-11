"""Handler helper tests — cookie/login tiers + DB task lifecycle + single-step handlers.

Covers the handlers.py functions not exercised by test_integration's scrape_task
flow: _try_cookie_recovery, _import_cookies, _update_account_status,
_check_account_valid, _promote_to_running, _set_task_need_human, _set_task_paused,
_update_task_progress, _load_task, _complete_task, _get_engine, handler_login
tiers, handler_validate, handler_search/detail/comments.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import semilabs_hone.modules.collection.handlers as h_mod


def _cap():
    out = []
    return (lambda m, d=None: out.append((m, d))), out


def _make_account(db_session, *, platform="xiaohongshu", nickname="acct"):
    from semilabs_hone.core.models.account import Account
    acct = Account(platform=platform, nickname=nickname)
    db_session.add(acct)
    db_session.commit()
    return acct.id


def _make_task(db_session, *, status="pending", max_posts=10):
    from semilabs_hone.core.models.task import CollectionTask
    t = CollectionTask(account_id=1, platform="xiaohongshu",
                       status=status, max_posts_per_keyword=max_posts)
    db_session.add(t)
    db_session.commit()
    return t.id


# ─── cookie / account helpers ────────────────────────────────────────────

class TestCookieRecovery:
    def test_no_cookie_file_returns_false(self, tmp_data_dir):
        cap, out = _cap()
        assert h_mod._try_cookie_recovery(7, "xiaohongshu", cap) is False
        assert any(m == "login_recovery_no_cookies" for m, _ in out)

    def test_found_cookies_returns_true(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_3" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"name": "sid", "value": "x"}]))
        cap, out = _cap()
        assert h_mod._try_cookie_recovery(3, "xiaohongshu", cap) is True
        assert any(m == "login_recovery_found_cookies" for m, _ in out)

    def test_corrupt_cookie_returns_false(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_4" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        assert h_mod._try_cookie_recovery(4, "xiaohongshu", lambda *a: None) is False


class TestImportCookies:
    def test_persists_cookies_and_dir(self, tmp_data_dir):
        from config import DATA_DIR
        cookies = [{"name": "sid", "value": "v"}]
        cap, out = _cap()
        h_mod._import_cookies(8, "xiaohongshu", cookies, cap)
        p = DATA_DIR / "collection" / "profiles" / "acct_8" / "cookies.json"
        assert p.exists()
        assert json.loads(p.read_text()) == cookies
        assert any(m == "login_cookies_imported" for m, _ in out)


class TestUpdateAccountStatus:
    def test_updates_existing_account(self, db_session, tmp_data_dir):
        aid = _make_account(db_session, nickname="up")
        cap, out = _cap()
        h_mod._update_account_status(aid, "active", cap)

        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            acct = sess.query(Account).filter(Account.id == aid).first()
            assert acct.status == "active"
            assert acct.last_login_at is not None
        finally:
            sess.close()
        assert any(m == "account_status_updated" for m, _ in out)

    def test_missing_account_no_raise(self, db_session, tmp_data_dir):
        h_mod._update_account_status(999999, "active", lambda *a: None)


class TestCheckAccountValid:
    def test_no_cookies_invalid(self, tmp_data_dir):
        assert h_mod._check_account_valid(11, "xiaohongshu", lambda *a: None) is False

    def test_valid_cookies(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_12" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"name": "x"}]))
        assert h_mod._check_account_valid(12, "xiaohongshu", lambda *a: None) is True

    def test_empty_cookies_invalid(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_13" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([]))
        assert h_mod._check_account_valid(13, "xiaohongshu", lambda *a: None) is False

    def test_corrupt_cookies_invalid(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_14" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{bad")
        assert h_mod._check_account_valid(14, "xiaohongshu", lambda *a: None) is False


# ─── task lifecycle helpers ──────────────────────────────────────────────

class TestTaskLifecycle:
    def test_promote_pending_to_running(self, db_session, tmp_data_dir):
        cap, out = _cap()
        tid = _make_task(db_session, status="pending")
        h_mod._promote_to_running(tid, cap)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(CollectionTask.id == tid).first()
            assert t.status == "running"
        finally:
            sess.close()
        assert any(m == "task_promoted" for m, _ in out)

    def test_promote_non_pending_skipped(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="completed")
        h_mod._promote_to_running(tid, lambda *a: None)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(CollectionTask.id == tid).first()
            assert t.status == "completed"  # unchanged
        finally:
            sess.close()

    def test_promote_none_noop(self, db_session, tmp_data_dir):
        h_mod._promote_to_running(None, lambda *a: None)

    def test_set_need_human(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="running")
        h_mod._set_task_need_human(tid, lambda *a: None)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            assert sess.query(CollectionTask).filter(
                CollectionTask.id == tid).first().status == "need_human"
        finally:
            sess.close()

    def test_set_paused(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="running")
        h_mod._set_task_paused(tid, lambda *a: None)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            assert sess.query(CollectionTask).filter(
                CollectionTask.id == tid).first().status == "paused"
        finally:
            sess.close()

    def test_update_task_progress(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="running")
        h_mod._update_task_progress(tid, 5, 5, lambda *a: None)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(CollectionTask.id == tid).first()
            assert t.last_note_index == 5
            assert t.actual_count == 5
        finally:
            sess.close()

    def test_load_task_found(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="running")
        d = h_mod._load_task(tid)
        assert d is not None
        assert d["id"] == tid
        assert d["status"] == "running"

    def test_load_task_none_id(self, db_session, tmp_data_dir):
        assert h_mod._load_task(None) is None

    def test_load_task_not_found(self, db_session, tmp_data_dir):
        assert h_mod._load_task("nonexistent-uuid") is None

    def test_complete_task(self, db_session, tmp_data_dir):
        tid = _make_task(db_session, status="running")
        h_mod._complete_task(tid, 7, 3, 7, lambda *a: None)
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            t = sess.query(CollectionTask).filter(CollectionTask.id == tid).first()
            assert t.status == "completed"
            assert t.actual_count == 7
            assert t.completed_at is not None
        finally:
            sess.close()


# ─── _get_engine ──────────────────────────────────────────────────────────

class TestGetEngine:
    def test_unknown_platform_returns_none(self, tmp_data_dir):
        assert h_mod._get_engine("nope_platform", None, lambda *a: None) is None

    def test_known_platform_returns_engine(self, tmp_data_dir):
        # xiaohongshu is registered (S5 yaml). Requires registry init.
        from semilabs_hone.modules.collection.scrapers.registry import get as reg_get
        try:
            spec, _ = reg_get("xiaohongshu")
            assert spec is not None
        except KeyError:
            pytest.skip("xiaohongshu not registered")
        eng = h_mod._get_engine("xiaohongshu", None, lambda *a: None)
        assert eng is not None


# ─── handler_login tiers ──────────────────────────────────────────────────

class TestHandlerLoginTiers:
    async def test_cookie_recovery_success(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_21" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"name": "sid"}]))
        cap, out = _cap()
        res = await h_mod.handler_login(
            {"platform": "xiaohongshu", "account_id": 21, "method": "auto"}, cap)
        assert res["status"] == "ok"
        assert res["login_method"] == "cookie_recovery"

    async def test_cookie_import_tier(self, tmp_data_dir):
        cap, out = _cap()
        res = await h_mod.handler_login(
            {"platform": "xiaohongshu", "account_id": 22, "method": "cookie_import",
             "cookies": [{"name": "sid"}]}, cap)
        assert res["status"] == "ok"
        assert res["login_method"] == "cookie_import"

    async def test_auto_falls_through_to_qr(self, tmp_data_dir):
        cap, out = _cap()
        res = await h_mod.handler_login(
            {"platform": "xiaohongshu", "account_id": 23, "method": "auto"}, cap)
        assert res["status"] == "ok"
        assert res["login_method"] == "qrcode"

    async def test_qrcode_method(self, tmp_data_dir):
        cap, out = _cap()
        res = await h_mod.handler_login(
            {"platform": "xiaohongshu", "account_id": 24, "method": "qrcode"}, cap)
        assert res["status"] == "ok"
        assert "qr_path" in res

    async def test_all_methods_fail_raises_login_error(self, tmp_data_dir):
        from semilabs_hone.core.utils.retry import LoginError
        # method=cookie_recovery but no cookies → LoginError (no QR fallback for
        # explicit non-auto method).
        with pytest.raises(LoginError):
            await h_mod.handler_login(
                {"platform": "xiaohongshu", "account_id": 25, "method": "cookie_recovery"},
                lambda *a: None)


# ─── single-step handlers ─────────────────────────────────────────────────

class TestSingleStepHandlers:
    async def test_handler_search_returns_items(self, monkeypatch):
        async def mock_search(self, kw, sort):
            from semilabs_hone.core.models.schemas import ItemRef
            return [ItemRef(platform="xiaohongshu", item_id="a")]
        from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
        monkeypatch.setattr(GenericEngine, "search", mock_search)
        orig = h_mod._get_engine
        h_mod._get_engine = lambda platform, account_id, cb: GenericEngine(spec=None)
        try:
            cap, out = _cap()
            res = await h_mod.handler_search(
                {"platform": "xiaohongshu", "keyword": "k"}, cap)
            assert res["status"] == "ok"
            assert res["items"][0]["item_id"] == "a"
        finally:
            h_mod._get_engine = orig

    async def test_handler_search_no_engine_raises(self, monkeypatch):
        from semilabs_hone.core.utils.retry import BrowserClosedError
        monkeypatch.setattr(h_mod, "_get_engine", lambda *a: None)
        with pytest.raises(BrowserClosedError):
            await h_mod.handler_search({"platform": "nope"}, lambda *a: None)

    async def test_handler_detail_returns_post(self, monkeypatch):
        async def mock_fetch(self, ref):
            from semilabs_hone.core.models.schemas import ScrapedPost
            return ScrapedPost(platform_id=ref.item_id, title="t", content="c")
        from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
        monkeypatch.setattr(GenericEngine, "fetch_item", mock_fetch)
        orig = h_mod._get_engine
        h_mod._get_engine = lambda platform, account_id, cb: GenericEngine(spec=None)
        try:
            res = await h_mod.handler_detail(
                {"platform": "xiaohongshu", "item_id": "x1", "download_images": False},
                lambda *a: None)
            assert res["status"] == "ok"
            assert res["post"]["platform_id"] == "x1"
        finally:
            h_mod._get_engine = orig

    async def test_handler_comments_returns_list(self, monkeypatch):
        async def mock_comments(self, ref):
            return [{"author": "u", "content": "hi"}]
        from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
        monkeypatch.setattr(GenericEngine, "fetch_comments", mock_comments)
        orig = h_mod._get_engine
        h_mod._get_engine = lambda platform, account_id, cb: GenericEngine(spec=None)
        try:
            res = await h_mod.handler_comments(
                {"platform": "xiaohongshu", "item_id": "c1"}, lambda *a: None)
            assert res["status"] == "ok"
            assert res["comments"][0]["author"] == "u"
        finally:
            h_mod._get_engine = orig


# ─── handler_validate ────────────────────────────────────────────────────

class TestHandlerValidate:
    async def test_validate_no_cookies_invalid(self, tmp_data_dir):
        res = await h_mod.handler_validate(
            {"platform": "xiaohongshu", "account_id": 31}, lambda *a: None)
        assert res["valid"] is False

    async def test_validate_with_cookies_valid(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_32" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"name": "sid"}]))
        res = await h_mod.handler_validate(
            {"platform": "xiaohongshu", "account_id": 32}, lambda *a: None)
        assert res["valid"] is True
