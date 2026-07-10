"""Tests for risk_probes.probe (PRD §4.4.1 / T24).

Mock page exposes query_selector + url. Probes must never raise on a flaky
selector (PRD §4.3.1 fallback extends to probes).
"""
from __future__ import annotations

import pytest

from semilabs_hone.modules.collection.risk_probes import ProbeHit, probe


class _MockPage:
    """Minimal page mock: a url + a set of "present" selectors."""

    def __init__(self, url: str = "https://www.xiaohongshu.com/explore/123",
                 present: set[str] | None = None,
                 raise_on: set[str] | None = None):
        self.url = url
        self._present = present or set()
        self._raise_on = raise_on or set()

    async def query_selector(self, sel: str):
        if sel in self._raise_on:
            raise RuntimeError("flaky selector")
        return object() if sel in self._present else None


# ─── XHS captcha probe ────────────────────────────────────────────────────


class TestXhsCaptchaProbe:
    async def test_probe_xhs_captcha_class_returns_hit(self):
        page = _MockPage(present={'[class*="captcha"]'})
        hit = await probe(page, "xiaohongshu")
        assert isinstance(hit, ProbeHit)
        assert hit.kind == "captcha"
        assert hit.platform == "xiaohongshu"

    async def test_probe_xhs_verify_slider_returns_hit(self):
        page = _MockPage(present={'[class*="verify-slider"]'})
        hit = await probe(page, "xiaohongshu")
        assert hit is not None
        assert hit.kind == "captcha"

    async def test_probe_xhs_clean_page_returns_none(self):
        page = _MockPage()  # no captcha selectors present
        hit = await probe(page, "xiaohongshu")
        assert hit is None


# ─── Zhihu login-expired probe ────────────────────────────────────────────


class TestZhihuLoginExpiredProbe:
    async def test_probe_zhihu_signin_redirect_returns_hit(self):
        page = _MockPage(url="https://www.zhihu.com/signin?next=%2F")
        hit = await probe(page, "zhihu")
        assert hit is not None
        assert hit.kind == "login_expired"
        assert hit.platform == "zhihu"

    async def test_probe_zhihu_login_wall_dom_returns_hit(self):
        page = _MockPage(url="https://www.zhihu.com/question/123",
                         present={'[class*="Login"]'})
        hit = await probe(page, "zhihu")
        assert hit is not None
        assert hit.kind == "login_expired"

    async def test_probe_zhihu_clean_page_returns_none(self):
        page = _MockPage(url="https://www.zhihu.com/question/123")
        hit = await probe(page, "zhihu")
        assert hit is None


# ─── Generic scan-to-login QR probe ────────────────────────────────────────


class TestQrLoginProbe:
    async def test_probe_qr_qrcode_class_returns_hit(self):
        page = _MockPage(present={'[class*="qrcode"]'})
        hit = await probe(page, "xiaohongshu")
        assert hit is not None
        assert hit.kind == "qr_login"


# ─── Defensive: flaky selector never raises ───────────────────────────────


class TestProbeRobustness:
    async def test_probe_flaky_selector_does_not_raise(self):
        """A selector that throws must be swallowed (no abort)."""
        page = _MockPage(present={'[class*="captcha"]'},
                         raise_on={'[class*="captcha"]'})
        # Should not raise; may still find the hit via another selector or none.
        hit = await probe(page, "xiaohongshu")
        assert isinstance(hit, (ProbeHit, type(None)))

    async def test_probe_page_without_query_selector_attr(self):
        """A page object lacking query_selector must not crash the probe."""

        class Bare:
            url = "https://www.xiaohongshu.com/explore/123"

        hit = await probe(Bare(), "xiaohongshu")
        assert hit is None
