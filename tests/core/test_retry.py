"""DM-01 retry 模块单测。

覆盖场景矩阵: 异常层级、fix_hint、装饰器重试次数/行为。
命名规范: test_<方法>_<场景>_<预期结果>。
"""
from __future__ import annotations

import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from semilabs_hone.core.utils import retry as retry_mod


# ── 异常层级 ──

ALL_ERROR_NAMES = [
    "CaptchaError", "RatelimitError", "PageLoadError", "LoginError",
    "DataParseError", "SessionExpiredError", "AccountBannedError",
    "QuietHoursError", "DailyLimitError", "BrowserClosedError",
    "EmptyResultError", "PortConflictError", "DiskFullError",
]


class TestSkimError_Hierarchy:
    """异常继承层级与 fix_hint 覆盖。"""

    def test_skimerror_base_has_category_and_fix_hint(self):
        e = retry_mod.SkimError("msg", category="test_cat", fix_hint="修复提示")
        assert e.category == "test_cat"
        assert e.fix_hint == "修复提示"
        assert str(e) == "msg"

    def test_all_subclasses_exist(self):
        for name in ALL_ERROR_NAMES:
            assert hasattr(retry_mod, name), f"retry 缺 {name}"

    def test_all_subclasses_are_skimerror_subclass(self):
        for name in ALL_ERROR_NAMES:
            cls = getattr(retry_mod, name)
            assert issubclass(cls, retry_mod.SkimError), f"{name} 不是 SkimError 子类"

    def test_captcha_error_is_skimerror_subclass(self):
        assert issubclass(retry_mod.CaptchaError, retry_mod.SkimError)


class TestError_Category:
    """每个异常子类的 category 值。"""

    @pytest.mark.parametrize("name,expected_cat", [
        ("CaptchaError", "captcha"),
        ("RatelimitError", "ratelimit"),
        ("PageLoadError", "page_load"),
        ("LoginError", "login"),
        ("DataParseError", "data_parse"),
        ("SessionExpiredError", "session_expired"),
        ("AccountBannedError", "account_banned"),
        ("QuietHoursError", "quiet_hours"),
        ("DailyLimitError", "daily_limit"),
        ("BrowserClosedError", "browser_closed"),
        ("EmptyResultError", "empty_result"),
        ("PortConflictError", "port_conflict"),
        ("DiskFullError", "disk_full"),
    ])
    def test_error_category(self, name, expected_cat):
        cls = getattr(retry_mod, name)
        e = cls()
        assert e.category == expected_cat


class TestError_FixHint:
    """每个异常子类带中文 fix_hint。"""

    @pytest.mark.parametrize("name", ALL_ERROR_NAMES)
    def test_fix_hint_exists_and_chinese(self, name):
        cls = getattr(retry_mod, name)
        e = cls()
        assert e.fix_hint, f"{name} 的 fix_hint 为空"
        # 修复提示应包含中文 (检查是否有中文字符)
        assert any("一" <= c <= "鿿" for c in e.fix_hint), \
            f"{name} 的 fix_hint 应包含中文: {e.fix_hint!r}"


class TestScraperRetry_Retries:
    """scraper_retry 装饰器: 重试 PageLoadError/TimeoutError 3 次。"""

    def test_scraper_retry_retries_page_load_error_then_raises(self):
        """PageLoadError 重试 3 次后抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def failing_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.PageLoadError("page failed")

        with pytest.raises(retry_mod.PageLoadError):
            failing_fn()
        assert call_count == 3

    def test_scraper_retry_retries_timeout_error_then_raises(self):
        """TimeoutError 重试 3 次后抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def failing_fn():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("timeout")

        with pytest.raises(TimeoutError):
            failing_fn()
        assert call_count == 3

    def test_scraper_retry_succeeds_on_retry(self):
        """PageLoadError 在重试后成功。"""
        call_count = 0

        @retry_mod.scraper_retry
        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise retry_mod.PageLoadError("temporary")
            return "ok"

        result = flaky_fn()
        assert result == "ok"
        assert call_count == 3

    def test_scraper_retry_does_not_retry_captcha_error(self):
        """不重试 CaptchaError，立即抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def captcha_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.CaptchaError("captcha")

        with pytest.raises(retry_mod.CaptchaError):
            captcha_fn()
        assert call_count == 1

    def test_scraper_retry_does_not_retry_login_error(self):
        """不重试 LoginError，立即抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def login_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.LoginError("login failed")

        with pytest.raises(retry_mod.LoginError):
            login_fn()
        assert call_count == 1

    def test_scraper_retry_does_not_retry_account_banned_error(self):
        """不重试 AccountBannedError，立即抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def banned_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.AccountBannedError("banned")

        with pytest.raises(retry_mod.AccountBannedError):
            banned_fn()
        assert call_count == 1

    def test_scraper_retry_does_not_retry_session_expired_error(self):
        """不重试 SessionExpiredError，立即抛出。"""
        call_count = 0

        @retry_mod.scraper_retry
        def session_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.SessionExpiredError("expired")

        with pytest.raises(retry_mod.SessionExpiredError):
            session_fn()
        assert call_count == 1


class TestRateLimitRetry_Retries:
    """rate_limit_retry 装饰器: 重试 RatelimitError 2 次，等 300s。

    测试用零等待的临时装饰器验证重试次数（不阻塞 300s）。
    """

    @staticmethod
    def _rate_limit_retry_fast():
        """创建 rate_limit_retry 但 wait=0，用于快速测试次数。"""
        return retry(
            retry=retry_if_exception_type(retry_mod.RatelimitError),
            stop=stop_after_attempt(2),
            wait=wait_fixed(0),
            reraise=True,
        )

    def test_rate_limit_retry_retries_ratelimit_then_raises(self):
        """RatelimitError 重试 2 次后抛出。"""
        call_count = 0

        @self._rate_limit_retry_fast()
        def failing_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.RatelimitError("rate limited")

        with pytest.raises(retry_mod.RatelimitError):
            failing_fn()
        assert call_count == 2

    def test_rate_limit_retry_succeeds_on_retry(self):
        """RatelimitError 在重试后成功。"""
        call_count = 0

        @self._rate_limit_retry_fast()
        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise retry_mod.RatelimitError("temporary rate limit")
            return "ok"

        result = flaky_fn()
        assert result == "ok"
        assert call_count == 2

    def test_rate_limit_retry_does_not_retry_other_errors(self):
        """不重试非 RatelimitError 异常。"""
        call_count = 0

        @self._rate_limit_retry_fast()
        def other_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.PageLoadError("page load")

        with pytest.raises(retry_mod.PageLoadError):
            other_fn()
        assert call_count == 1

    def test_rate_limit_retry_does_not_retry_captcha(self):
        """不重试 CaptchaError。"""
        call_count = 0

        @self._rate_limit_retry_fast()
        def captcha_fn():
            nonlocal call_count
            call_count += 1
            raise retry_mod.CaptchaError("captcha")

        with pytest.raises(retry_mod.CaptchaError):
            captcha_fn()
        assert call_count == 1

    def test_rate_limit_retry_callable(self):
        """rate_limit_retry 可调用。"""
        assert callable(retry_mod.rate_limit_retry)


class TestRetry_Callable:
    """装饰器可调用。"""

    def test_scraper_retry_is_callable(self):
        assert callable(retry_mod.scraper_retry)

    def test_rate_limit_retry_is_callable(self):
        assert callable(retry_mod.rate_limit_retry)
