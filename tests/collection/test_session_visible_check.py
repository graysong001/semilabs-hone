"""验证即所见（2026-07-31）：_session_check 保留页面 + ctx 自愈 + cookies.json 重注。

背景：用户点「验证」长期无反馈 ——
1. handler_validate 的 validate_done 事件未映射到 WS session_status（前端断链）；
2. _session_check 开页检查后立即 close，用户看不到登录状态；
3. 用户手动关掉 Chrome 后 ctx 死亡（Target closed），后续登录/验证静默失败；
4. Chrome 重启丢 session cookie，验证前不重注 cookies.json。
"""
from __future__ import annotations

import asyncio
import json

import pytest

import semilabs_hone.modules.collection.handlers as h_mod


# ─── fakes ───────────────────────────────────────────────────────────────

class _FakeBrowser:
    def __init__(self, connected: bool = True):
        self._connected = connected

    def is_connected(self):
        return self._connected


class _FakePage:
    def __init__(self, url: str = "about:blank", dom: dict | None = None):
        self.url = url
        self.dom = dom if dom is not None else {"loggedIn": False, "modal": False}
        self.front_called = 0
        self.close_called = 0

    async def goto(self, url, **_kw):
        self.url = url

    async def evaluate(self, *_a, **_kw):
        return self.dom

    async def bring_to_front(self):
        self.front_called += 1

    async def close(self):
        self.close_called += 1


class _FakeCtx:
    def __init__(self, pages=None, cookies=None, connected: bool | None = None,
                 page_dom: dict | None = None):
        self.pages = list(pages or [])
        self._cookies = [{"name": "a"}] if cookies is None else cookies
        self.added: list = []
        self._page_dom = page_dom
        # connected=None → 无 browser 属性（历史 fake，视为存活）
        self._browser = None if connected is None else _FakeBrowser(connected)

    @property
    def browser(self):
        return self._browser

    async def cookies(self, _url=None):
        return self._cookies

    async def add_cookies(self, cookies):
        self.added.extend(cookies)

    async def new_page(self):
        p = _FakePage(dom=self._page_dom)
        self.pages.append(p)
        return p


class _FakeProc:
    def __init__(self):
        self.pid = 424242
        self.terminated = 0

    def terminate(self):
        self.terminated += 1


@pytest.fixture(autouse=True)
def _clean_relaunched_procs():
    yield
    h_mod._RELAUNCHED_CHROME_PROCS.clear()


# ─── _ctx_alive / _ensure_live_ctx ───────────────────────────────────────

class TestCtxAlive:
    def test_no_browser_attr_treated_alive(self):
        assert h_mod._ctx_alive(object()) is True

    def test_connected_browser_alive(self):
        assert h_mod._ctx_alive(_FakeCtx(connected=True)) is True

    def test_disconnected_browser_dead(self):
        assert h_mod._ctx_alive(_FakeCtx(connected=False)) is False


class TestEnsureLiveCtx:
    async def test_none_ctx_never_launches(self, monkeypatch):
        monkeypatch.setattr(h_mod, "_WORKER_CTX", None)
        assert await h_mod._ensure_live_ctx(1, lambda *a: None) is None

    async def test_alive_ctx_returned_as_is(self, monkeypatch):
        ctx = _FakeCtx(connected=True)
        monkeypatch.setattr(h_mod, "_WORKER_CTX", ctx)
        assert await h_mod._ensure_live_ctx(1, lambda *a: None) is ctx

    async def test_dead_ctx_relaunches_chrome(self, monkeypatch, tmp_data_dir):
        from semilabs_hone.modules.collection.browser import cdp as cdp_mod

        dead = _FakeCtx(connected=False)
        monkeypatch.setattr(h_mod, "_WORKER_CTX", dead)
        monkeypatch.setattr(h_mod, "_WORKER_ACCOUNT", None)

        new_ctx = _FakeCtx(connected=True)
        proc = _FakeProc()
        calls = {}

        monkeypatch.setattr(cdp_mod, "find_free_port", lambda: 19999)
        def _launch(profile_dir, port):
            calls["launch"] = (profile_dir, port)
            return proc
        monkeypatch.setattr(cdp_mod, "launch_real_chrome", _launch)

        async def _attach(port, timeout=cdp_mod.ATTACH_TIMEOUT):
            calls["attach"] = port
            return object(), new_ctx
        monkeypatch.setattr(cdp_mod, "attach", _attach)

        events = []
        got = await h_mod._ensure_live_ctx(7, lambda m, d=None: events.append(m))

        assert got is new_ctx
        assert h_mod._WORKER_CTX is new_ctx  # republished
        assert calls["launch"][1] == 19999 and calls["attach"] == 19999
        assert "browser_relaunch" in events
        assert h_mod._RELAUNCHED_CHROME_PROCS == [proc]

        h_mod.terminate_relaunched_chromes()
        assert proc.terminated == 1
        assert h_mod._RELAUNCHED_CHROME_PROCS == []

    async def test_relaunch_failure_returns_none(self, monkeypatch, tmp_data_dir):
        from semilabs_hone.modules.collection.browser import cdp as cdp_mod

        monkeypatch.setattr(h_mod, "_WORKER_CTX", _FakeCtx(connected=False))
        monkeypatch.setattr(h_mod, "_WORKER_ACCOUNT", None)
        monkeypatch.setattr(cdp_mod, "find_free_port", lambda: 19998)

        def _boom(_dir, _port):
            raise RuntimeError("no chrome")
        monkeypatch.setattr(cdp_mod, "launch_real_chrome", _boom)

        assert await h_mod._ensure_live_ctx(8, lambda *a: None) is None


# ─── _load_cookies_into_ctx ──────────────────────────────────────────────

class TestLoadCookiesIntoCtx:
    async def test_injects_normalized_cookies_from_disk(self, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "5" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{
            "name": "web_session", "value": "abc", "domain": ".xiaohongshu.com",
            "path": "/", "sameSite": "unspecified", "expirationDate": 1780000000,
            "hostOnly": False, "storeId": "0",
        }]))

        ctx = _FakeCtx()
        events = []
        await h_mod._load_cookies_into_ctx(ctx, 5, lambda m, d=None: events.append(m))

        assert len(ctx.added) == 1
        assert ctx.added[0]["sameSite"] == "None"  # 归一化生效
        assert "hostOnly" not in ctx.added[0]
        assert "session_check_cookies_loaded" in events

    async def test_missing_file_is_noop(self, tmp_data_dir):
        ctx = _FakeCtx()
        await h_mod._load_cookies_into_ctx(ctx, 404, lambda *a: None)
        assert ctx.added == []


# ─── _platform_page ──────────────────────────────────────────────────────

class TestPlatformPage:
    async def test_reuses_same_site_tab(self):
        xhs = _FakePage("https://www.xiaohongshu.com/explore")
        ctx = _FakeCtx(pages=[_FakePage("https://www.zhihu.com/"), xhs])
        got = await h_mod._platform_page(ctx, "https://www.xiaohongshu.com")
        assert got is xhs

    async def test_reuses_blank_tab_before_new_page(self):
        blank = _FakePage("about:blank")
        ctx = _FakeCtx(pages=[blank])
        got = await h_mod._platform_page(ctx, "https://www.xiaohongshu.com")
        assert got is blank  # 不为首个验证多开一个 tab

    async def test_falls_back_to_new_page(self):
        ctx = _FakeCtx(pages=[_FakePage("https://www.zhihu.com/")])
        got = await h_mod._platform_page(ctx, "https://www.xiaohongshu.com")
        assert got is ctx.pages[-1] and len(ctx.pages) == 2


# ─── _session_check：保留页面 + DOM 判定 + cookies.json 重注 ───────────

class TestSessionCheckVisible:
    def _install_ctx(self, monkeypatch, ctx):
        monkeypatch.setattr(h_mod, "_WORKER_CTX", ctx)

    async def test_logged_in_dom_keeps_page_on_home(self, monkeypatch, tmp_data_dir):
        ctx = _FakeCtx(page_dom={"loggedIn": True, "modal": False})
        self._install_ctx(monkeypatch, ctx)

        ok = await h_mod._session_check("xiaohongshu", 9, lambda *a: None)

        assert ok is True
        page = ctx.pages[0]
        assert page.url == "https://www.xiaohongshu.com"  # 停在平台首页
        assert page.front_called == 1  # 带到前台给用户看
        assert page.close_called == 0  # 不再秒关（验证即所见）

    async def test_login_modal_dom_means_logged_out(self, monkeypatch, tmp_data_dir):
        ctx = _FakeCtx(page_dom={"loggedIn": False, "modal": True})
        self._install_ctx(monkeypatch, ctx)

        events = []
        ok = await h_mod._session_check("xiaohongshu", 9, lambda m, d=None: events.append(m))

        assert ok is False
        assert "session_check_login_modal_shown" in events

    async def test_dom_unknown_falls_back_to_identity(self, monkeypatch, tmp_data_dir):
        """DOM 无信号（页面未渲染完）→ identity_api 兜底判定。"""
        monkeypatch.setattr(h_mod, "_DOM_DETECT_TIMEOUT_S", 0.2)
        monkeypatch.setattr(h_mod, "_DOM_DETECT_POLL_S", 0.05)
        ctx = _FakeCtx(page_dom={"loggedIn": False, "modal": False})
        self._install_ctx(monkeypatch, ctx)

        async def _identity(_ctx, _platform):
            return {"platform_user_id": "u1", "platform_nickname": "薯薯"}
        monkeypatch.setattr(h_mod, "_extract_platform_identity", _identity)

        assert await h_mod._session_check("xiaohongshu", 9, lambda *a: None) is True

    async def test_no_cookies_short_circuits(self, monkeypatch, tmp_data_dir):
        ctx = _FakeCtx(cookies=[])
        self._install_ctx(monkeypatch, ctx)
        events = []
        ok = await h_mod._session_check("xiaohongshu", 9, lambda m, d=None: events.append(m))
        assert ok is False
        assert "session_check_no_cookies" in events
        assert ctx.pages == []  # 没开页

    async def test_reinjects_cookies_json_before_check(self, monkeypatch, tmp_data_dir):
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "9" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([
            {"name": "web_session", "value": "s", "domain": ".xiaohongshu.com"}]))

        ctx = _FakeCtx(page_dom={"loggedIn": True, "modal": False})
        self._install_ctx(monkeypatch, ctx)

        assert await h_mod._session_check("xiaohongshu", 9, lambda *a: None) is True
        assert any(c["name"] == "web_session" for c in ctx.added)


# ─── handler_validate：WS 断链修复（validate_done → session_status）──────

class TestValidateEmitsSessionStatus:
    async def test_session_status_event_carries_valid_and_message(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr(h_mod, "_WORKER_CTX", None)  # degraded 磁盘路径
        from config import DATA_DIR
        p = DATA_DIR / "collection" / "profiles" / "acct_41" / "cookies.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([{"name": "sid"}]))

        events = []
        result = await h_mod.handler_validate(
            {"platform": "xiaohongshu", "account_id": 41},
            lambda m, d=None: events.append((m, d)))

        assert result["valid"] is True
        ss = [d for m, d in events if m == "session_status"]
        assert ss, "validate 必须发 session_status（accounts.html/app.js 监听此事件）"
        assert ss[0]["valid"] is True
        assert "会话有效" in ss[0]["message"]


# ─── WS relay：session_status 映射 + 顶层 valid 展平 ─────────────────────

class TestRelaySessionStatus:
    async def test_session_status_progress_relayed_with_top_level_valid(
            self, tmp_data_dir, monkeypatch):
        from semilabs_hone.core.ipc.paths import atomic_write_json, progress_path
        from semilabs_hone.core.ipc.protocol import IPCProgress
        from semilabs_hone.core.ui import ws as ws_mod

        rid = "relay-session-status"
        prog = IPCProgress(request_id=rid, message="session_status", data={
            "account_id": 1, "valid": True,
            "message": "会话有效，已在浏览器中打开平台页面",
        })
        atomic_write_json(progress_path(rid), prog.model_dump())

        broadcasts: list = []

        async def _fake_broadcast(msg):
            broadcasts.append(msg)
        monkeypatch.setattr(ws_mod.ws_manager, "broadcast", _fake_broadcast)

        task = asyncio.create_task(ws_mod.run_progress_relay(interval=0.05))
        try:
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        msgs = [b for b in broadcasts if b.get("type") == "session_status"]
        assert msgs, "session_status 必须作为一等 WS 事件透出"
        m = msgs[0]
        assert m["valid"] is True  # app.js dispatch 读 msg.valid
        assert m["message"] == "会话有效，已在浏览器中打开平台页面"
        assert m["data"]["account_id"] == 1
