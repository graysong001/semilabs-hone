"""Cookie 归一化（Chrome 扩展导出格式 → Playwright）+ 注入验证链路测试。

2026-07-30 修复背景：cookie 插件导出的 sameSite="unspecified" 直接喂
add_cookies 必抛错且静默失败（用户绑定 cookie 后账号始终未激活）。
"""
import pytest

import semilabs_hone.modules.collection.handlers as h_mod


# ─── _normalize_cookies ──────────────────────────────────────────────────

class TestNormalizeCookies:
    def test_chrome_extension_format_full_mapping(self):
        """真实插件导出样本：多余字段剥离 + sameSite/expires 映射。"""
        raw = [{
            "domain": ".xiaohongshu.com", "expirationDate": 1780000000.123,
            "hostOnly": False, "httpOnly": True, "name": "web_session",
            "path": "/", "sameSite": "unspecified", "secure": True,
            "session": False, "storeId": "0", "value": "abc",
        }]
        (nc,) = h_mod._normalize_cookies(raw)
        assert nc == {
            "name": "web_session", "value": "abc",
            "domain": ".xiaohongshu.com", "path": "/",
            "secure": True, "httpOnly": True,
            "sameSite": "None", "expires": 1780000000.123,
        }

    def test_samesite_mapping_table(self):
        cases = {
            "unspecified": "None", "no_restriction": "None",
            "Strict": "Strict", "strict": "Strict",
            "LAX": "Lax", "None": "None", "": None, None: None,
        }
        for raw_ss, expect in cases.items():
            (nc,) = h_mod._normalize_cookies(
                [{"name": "a", "value": "v", "domain": "d", "sameSite": raw_ss}])
            if expect is None:
                assert "sameSite" not in nc
            else:
                assert nc["sameSite"] == expect

    def test_session_cookie_gets_expires_minus_one(self):
        (nc,) = h_mod._normalize_cookies(
            [{"name": "a", "value": "v", "domain": "d", "session": True}])
        assert nc["expires"] == -1

    def test_zero_or_invalid_expiry_becomes_session(self):
        for exp in (0, "not-a-number", None):
            (nc,) = h_mod._normalize_cookies(
                [{"name": "a", "value": "v", "domain": "d", "expirationDate": exp}])
            assert nc["expires"] == -1, exp

    def test_playwright_native_expires_field_respected(self):
        (nc,) = h_mod._normalize_cookies(
            [{"name": "a", "value": "v", "domain": "d", "expires": 1780000000}])
        assert nc["expires"] == 1780000000.0

    def test_path_defaults_and_non_dict_skipped(self):
        out = h_mod._normalize_cookies(
            [{"name": "a", "value": "v", "domain": "d"}, "garbage", 42])
        assert len(out) == 1
        assert out[0]["path"] == "/"


# ─── _import_cookies_ctx（新签名: (ok, reason)）─────────────────────────

class _FakeCtx:
    def __init__(self, add_error: Exception | None = None):
        self.add_error = add_error
        self.added: list | None = None

    async def add_cookies(self, cookies):
        if self.add_error:
            raise self.add_error
        self.added = cookies


def _mk_ctx(monkeypatch, ctx, *, session_ok=True):
    """Install a fake worker ctx + stub _session_check."""
    monkeypatch.setattr(h_mod, "_WORKER_CTX", ctx)

    async def fake_session_check(platform, account_id, progress_cb):
        return session_ok

    monkeypatch.setattr(h_mod, "_session_check", fake_session_check)
    return ctx


class TestImportCookiesCtx:
    async def test_success_returns_true_and_normalizes(self, monkeypatch):
        ctx = _mk_ctx(monkeypatch, _FakeCtx())
        ok, reason = await h_mod._import_cookies_ctx(
            "xiaohongshu", 1,
            [{"name": "a", "value": "v", "domain": "d", "sameSite": "unspecified"}],
            lambda *a: None)
        assert ok is True and reason is None
        # 注入的是归一化后的格式
        assert ctx.added[0]["sameSite"] == "None"
        assert "hostOnly" not in ctx.added[0]

    async def test_add_cookies_failure_surfaces_reason(self, monkeypatch):
        _mk_ctx(monkeypatch, _FakeCtx(add_error=ValueError("sameSite: expected one of")))
        events = []
        ok, reason = await h_mod._import_cookies_ctx(
            "xiaohongshu", 1, [{"name": "a", "value": "v", "domain": "d"}],
            lambda m, d=None: events.append(m))
        assert ok is False
        assert "Cookie 注入浏览器失败" in reason
        assert "cookie_import_failed" in events

    async def test_session_check_failure_means_expired_cookie(self, monkeypatch):
        _mk_ctx(monkeypatch, _FakeCtx(), session_ok=False)
        ok, reason = await h_mod._import_cookies_ctx(
            "xiaohongshu", 1, [{"name": "a", "value": "v", "domain": "d"}],
            lambda *a: None)
        assert ok is False
        assert "已失效" in reason

    async def test_no_ctx_returns_not_ready(self, monkeypatch):
        monkeypatch.setattr(h_mod, "_WORKER_CTX", None)
        ok, reason = await h_mod._import_cookies_ctx(
            "xiaohongshu", 1, [{"name": "a"}], lambda *a: None)
        assert ok is False
        assert "采集浏览器未就绪" in reason

    async def test_all_invalid_elements_rejected_early(self, monkeypatch):
        _mk_ctx(monkeypatch, _FakeCtx())
        ok, reason = await h_mod._import_cookies_ctx(
            "xiaohongshu", 1, ["garbage", 42], lambda *a: None)
        assert ok is False
        assert "为空或元素格式无效" in reason


# ─── handler_login Tier 3：失败携带具体原因 ─────────────────────────────

class TestCookieImportLoginError:
    async def test_import_failure_raises_login_error_with_reason(self, monkeypatch, tmp_data_dir):
        from semilabs_hone.core.utils.retry import LoginError
        _mk_ctx(monkeypatch, _FakeCtx(add_error=ValueError("boom")))
        with pytest.raises(LoginError, match="Cookie 注入浏览器失败"):
            await h_mod.handler_login(
                {"platform": "xiaohongshu", "account_id": 31,
                 "method": "cookie_import",
                 "cookies": [{"name": "a", "value": "v", "domain": "d"}]},
                lambda *a: None)
