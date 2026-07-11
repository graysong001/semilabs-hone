"""PRD §8.1 — 环境初始化与账号登录验收 (Environment & Auth).

BDD acceptance tests for scenarios 1.1 (未登录拦截) and 1.2 (端口冲突).
Each test names the Given/When/Then it asserts. These re-express the PRD
acceptance criteria at the verification layer, driving the real production
code paths (probe / cdp attach / worker_main exit).
"""
from __future__ import annotations

import pytest


class _MockPage:
    """Minimal page mock for risk_probes: a url + a set of present selectors."""

    def __init__(self, url: str = "https://www.xiaohongshu.com/explore/abc",
                 present: set[str] | None = None):
        self.url = url
        self._present = present or set()

    async def query_selector(self, sel: str):
        return object() if sel in self._present else None


# ─── 场景 1.1：首次启动未登录拦截 ──────────────────────────────────────

class TestScenario11NotLoggedInIntercept:
    """PRD §8.1 场景 1.1.

    Given 原生 Chrome 配置目录全新，未包含任何平台 Cookie.
    When  Worker 开始执行第一个抓取任务并打开首页.
    Then  Worker 必须在暖场阶段即通过探针发现页面存在登录弹窗或处于未登录状态.
    """

    async def test_login_redirect_detected_during_warmup(self):
        """A mid-task /login redirect is caught by the risk probe (login_expired).

        PRD 1.1 Then: 探针发现未登录状态. The probe's generic login-redirect
        heuristic (risk_probes._looks_like_login_redirect) fires on any /login,
        /signin, /passport marker — so a fresh-session redirect to a login page
        is detected and surfaces as login_expired → need_human relay.
        """
        from semilabs_hone.modules.collection.risk_probes import probe

        # Given: a page redirected to the platform login path (fresh session).
        page = _MockPage(url="https://www.xiaohongshu.com/login")

        # When: the probe runs (warmup fires it after each goto).
        hit = await probe(page, "xiaohongshu")

        # Then: it's a login_expired hit the handler maps to need_human.
        assert hit is not None
        assert hit.kind == "login_expired"
        assert hit.platform == "xiaohongshu"

    async def test_login_popup_dom_detected(self):
        """An on-page login wall DOM (QR / login-scan) is caught by the probe.

        PRD 1.1 Then: 探针发现页面存在登录弹窗. The QR-login selector set
        surfaces a scan-to-login QR as qr_login → need_human.
        """
        from semilabs_hone.modules.collection.risk_probes import probe

        page = _MockPage(url="https://www.xiaohongshu.com/explore/abc",
                         present={'[class*="qrcode"]'})
        hit = await probe(page, "xiaohongshu")

        assert hit is not None
        assert hit.kind == "qr_login"


# ─── 场景 1.2：浏览器端口冲突处理 ──────────────────────────────────────

class TestScenario12PortConflict:
    """PRD §8.1 场景 1.2.

    Given 用户日常自己打开了 Chrome，占用了 9222 调试端口.
    When  Worker 尝试通过 subprocess.Popen 拉起 Chrome.
    Then  Worker 必须捕获端口占用或连接 CDP 失败的异常，将任务置为 paused，
          并在 UI 抛出明确提示.
    """

    async def test_cdp_attach_failure_carries_port_busy_hint(self, monkeypatch):
        """A CDP connection failure raises CDPAttachError with the port-busy hint.

        PRD 1.2 Then: 捕获端口占用/连接 CDP 失败的异常 + 明确提示
        「Chrome 调试端口被占用，请关闭所有 Chrome 窗口后重试」.
        """
        import asyncio
        import sys
        from unittest.mock import patch
        from semilabs_hone.modules.collection.browser import cdp

        class _FakePW:
            class chromium:
                @staticmethod
                async def connect_over_cdp(endpoint):
                    raise ConnectionError("connect ECONNREFUSED")

            async def start(self):
                return self

        fake_pw_mod = type(sys)("playwright.async_api")
        fake_pw_mod.async_playwright = lambda: _FakePW()
        with patch.dict(sys.modules, {"playwright.async_api": fake_pw_mod}):
            with pytest.raises(cdp.CDPAttachError) as exc_info:
                await cdp.attach(9333)
        assert cdp.CDP_PORT_BUSY_HINT in str(exc_info.value)

    def test_worker_main_exits_nonzero_on_cdp_attach_error(self, tmp_data_dir, monkeypatch):
        """worker_main returns 1 on CDPAttachError → caller/heartbeat parks task as paused.

        PRD 1.2 Then: 将任务置为 paused. The worker exits non-zero; the web-side
        heartbeat watchdog reaps the zombie `running` task → paused + WS within 30s.
        """
        from unittest.mock import MagicMock
        from semilabs_hone.modules.collection.browser import cdp, worker_main

        async def fake_attach(port):
            raise cdp.CDPAttachError()

        monkeypatch.setattr(worker_main, "find_free_port", lambda: 9333)
        monkeypatch.setattr(worker_main, "launch_real_chrome",
                            lambda pd, p: MagicMock(pid=1))
        monkeypatch.setattr(worker_main, "ensure_profile",
                            lambda aid: tmp_data_dir / "collection" / "profiles" / str(aid))
        monkeypatch.setattr(worker_main, "attach", fake_attach)

        rc = worker_main.main(["--account", "1"])
        assert rc == 1
