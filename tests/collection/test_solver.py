"""Tests for captcha/solver.py detect_and_solve policy dispatch (PRD §4.4 / T26).

契约§5 验证码可选能力：
- 默认 manual → 命中即 paused（立即转人工 need_human），不动 slide/ocr
- 仅 anonymous + auto_then_manual 才自动解，失败 1 次转人工、不死循环
- account 站即便写了 auto_then_manual 也按 manual 处理（linter 另守 yaml 侧）

死循环红线（PRD §7.4）：solve_slide/solve_ocr 每次调用最多 1 次。
"""
from __future__ import annotations

import pytest

from semilabs_hone.modules.collection.captcha import solver as solver_mod
from semilabs_hone.modules.collection.captcha.solver import SolveResult, detect_and_solve


class _MockPage:
    """Minimal page mock: a set of "present" selectors (mirrors test_risk_probes)."""

    def __init__(self, present: set[str] | None = None) -> None:
        self._present = present or set()

    async def query_selector(self, sel: str):
        return object() if sel in self._present else None


# Selectors that _detect_captcha_type probes for each kind.
SLIDE_SEL = '[class*="slide"]'
OCR_SEL = "img[src*='captcha']"
CLICK_SEL = '[class*="click-verify"]'


# ─── No captcha ───────────────────────────────────────────────────────────


class TestNoCaptcha:
    async def test_clean_page_returns_solved(self):
        res = await detect_and_solve(_MockPage())
        assert isinstance(res, SolveResult)
        assert res.status == "solved"


# ─── Default = manual → immediate need_human, no auto attempt ─────────────


class TestManualPolicy:
    async def test_default_params_captcha_present_immediately_paused(
        self, monkeypatch
    ):
        """No risk_tier/captcha_policy passed → manual default → paused, no solve call."""
        calls = {"slide": 0, "ocr": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=False),
        )
        monkeypatch.setattr(
            solver_mod.ocr_solver, "solve_ocr",
            _async_counter(calls, "ocr", ret=""),
        )

        res = await detect_and_solve(_MockPage({SLIDE_SEL}))

        assert res.status == "paused"
        assert res.captcha_type == "slide"
        assert calls["slide"] == 0  # manual → never auto-solves
        assert calls["ocr"] == 0

    async def test_explicit_manual_policy_does_not_auto_solve(self, monkeypatch):
        calls = {"slide": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=False),
        )

        res = await detect_and_solve(
            _MockPage({SLIDE_SEL}), risk_tier="anonymous", captcha_policy="manual"
        )

        assert res.status == "paused"
        assert calls["slide"] == 0

    async def test_account_tier_overrides_auto_then_manual(self, monkeypatch):
        """account + auto_then_manual must still behave as manual (linter guards yaml; solver is defensive)."""
        calls = {"slide": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=False),
        )

        res = await detect_and_solve(
            _MockPage({SLIDE_SEL}), risk_tier="account", captcha_policy="auto_then_manual"
        )

        assert res.status == "paused"
        assert calls["slide"] == 0  # account tier → no auto-solve

    async def test_manual_path_click_type_paused(self):
        res = await detect_and_solve(_MockPage({CLICK_SEL}))
        assert res.status == "paused"
        assert res.captcha_type == "click"


# ─── anonymous + auto_then_manual: one attempt, no death loop ─────────────


class TestAutoThenManual:
    async def test_slide_success_solved(self, monkeypatch):
        calls = {"slide": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=True),
        )

        res = await detect_and_solve(
            _MockPage({SLIDE_SEL}), risk_tier="anonymous", captcha_policy="auto_then_manual"
        )

        assert res.status == "solved"
        assert res.captcha_type == "slide"
        assert calls["slide"] == 1

    async def test_slide_fail_once_then_paused_no_loop(self, monkeypatch):
        """Fail → exactly 1 attempt → paused. PRD §7.4 no death loop."""
        calls = {"slide": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=False),
        )

        res = await detect_and_solve(
            _MockPage({SLIDE_SEL}), risk_tier="anonymous", captcha_policy="auto_then_manual"
        )

        assert res.status == "paused"
        assert calls["slide"] == 1  # not 2, not N

    async def test_ocr_success_solved(self, monkeypatch):
        calls = {"ocr": 0}
        monkeypatch.setattr(
            solver_mod.ocr_solver, "solve_ocr",
            _async_counter(calls, "ocr", ret="ABCD"),
        )
        # Decouple from image-extraction selectors: hand bytes directly.
        async def _fake_img(page):
            return b"png"
        monkeypatch.setattr(solver_mod, "_extract_captcha_image", _fake_img)

        res = await detect_and_solve(
            _MockPage({OCR_SEL}), risk_tier="anonymous", captcha_policy="auto_then_manual"
        )

        assert res.status == "solved"
        assert res.captcha_type == "ocr"
        assert calls["ocr"] == 1

    async def test_ocr_fail_once_then_paused(self, monkeypatch):
        calls = {"ocr": 0}
        monkeypatch.setattr(
            solver_mod.ocr_solver, "solve_ocr",
            _async_counter(calls, "ocr", ret=""),
        )
        async def _fake_img(page):
            return b"png"
        monkeypatch.setattr(solver_mod, "_extract_captcha_image", _fake_img)

        res = await detect_and_solve(
            _MockPage({OCR_SEL}), risk_tier="anonymous", captcha_policy="auto_then_manual"
        )

        assert res.status == "paused"
        assert calls["ocr"] == 1

    async def test_click_type_still_manual_under_auto_policy(self, monkeypatch):
        """click/sms are manual-only regardless of policy."""
        calls = {"slide": 0, "ocr": 0}
        monkeypatch.setattr(
            solver_mod.slide_solver, "solve_slide",
            _async_counter(calls, "slide", ret=False),
        )
        monkeypatch.setattr(
            solver_mod.ocr_solver, "solve_ocr",
            _async_counter(calls, "ocr", ret=""),
        )

        res = await detect_and_solve(
            _MockPage({CLICK_SEL}), risk_tier="anonymous", captcha_policy="auto_then_manual"
        )

        assert res.status == "paused"
        assert res.captcha_type == "click"
        assert calls["slide"] == 0 and calls["ocr"] == 0


# ─── helper ───────────────────────────────────────────────────────────────


def _async_counter(counter: dict, key: str, ret):
    """Return an async fake that counts calls under counter[key] and returns ret."""
    async def _fake(*args, **kwargs):
        counter[key] += 1
        return ret
    return _fake
