"""Captcha detection + policy-driven dispatcher (PRD §4.4 / 契约§5 / T26).

Detect captcha type on page -> dispatch per platform risk policy.

契约§5 验证码可选能力:
- 默认 manual (account 站): 命中即 paused (立即转人工 need_human), 不动 slide/ocr。
- 仅 risk_tier=anonymous + captcha_policy=auto_then_manual (cargo 类无登录站)
  才走 slide/ocr 自动解; 失败 1 次转人工、不死循环 (PRD §7.4)。
- click/sms/unknown 永远人工, 与策略无关。

Design: docs/skim_design.md §10 (风险分层).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import config
from loguru import logger

from semilabs_hone.core.utils.retry import CaptchaError
from semilabs_hone.modules.collection.captcha import manual_handler, ocr_solver, slide_solver

if TYPE_CHECKING:
    from playwright.async_api import Page


# captcha 类型中只能走人工的 (与策略无关)。
_MANUAL_ONLY_TYPES = {"click", "sms"}


class SolveResult:
    """Result of captcha detection + solve attempt."""

    def __init__(
        self,
        status: str,
        captcha_type: str | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status  # "solved" | "paused" | "failed"
        self.captcha_type = captcha_type
        self.error = error

    def __repr__(self) -> str:
        return f"SolveResult(status={self.status!r}, type={self.captcha_type!r})"


async def detect_and_solve(
    page: Any,
    ctx: Any = None,
    risk_tier: str | None = None,
    captcha_policy: str | None = None,
) -> SolveResult:
    """Detect captcha on the current page and attempt to solve per platform policy.

    Args:
        page: Playwright Page object.
        ctx: Optional IPC context for manual fallback.
        risk_tier: ``account`` | ``anonymous`` (default account / config default).
        captcha_policy: ``manual`` | ``auto_then_manual`` (default manual /
            ``config.CAPTCHA_DEFAULT_POLICY``).

    Returns:
        SolveResult with status: solved | paused | failed.
        ``paused`` = 立即转人工 need_human (调用方据此 sink + 等待 resume)。
    """
    captcha_type = await _detect_captcha_type(page)
    if captcha_type is None:
        logger.debug("No captcha detected")
        return SolveResult(status="solved")

    tier = risk_tier or "account"
    policy = captcha_policy or config.CAPTCHA_DEFAULT_POLICY
    # 契约§5: 仅 anonymous + auto_then_manual 才允许自动解。
    can_auto = (tier == "anonymous" and policy == "auto_then_manual")

    logger.info(
        f"Captcha detected: {captcha_type} (tier={tier}, policy={policy}, auto={can_auto})"
    )

    # 默认 manual / account 站 / click-sms / unknown → 立即转人工, 不自动解。
    if not can_auto or captcha_type in _MANUAL_ONLY_TYPES:
        await _request_manual(ctx, captcha_type)
        return SolveResult(status="paused", captcha_type=captcha_type)

    # anonymous + auto_then_manual: 恰好一次尝试, 失败转人工, 绝不重试 (PRD §7.4)。
    try:
        success = await _attempt_auto(page, captcha_type)
    except CaptchaError:
        raise
    except Exception as e:
        logger.error(f"Captcha auto-solve error: {e}")
        success = False

    if success:
        logger.info(f"Auto-solved captcha: {captcha_type}")
        return SolveResult(status="solved", captcha_type=captcha_type)

    logger.warning(f"Auto-solve failed once for {captcha_type} → manual relay")
    await _request_manual(ctx, captcha_type)
    return SolveResult(
        status="paused", captcha_type=captcha_type, error="auto_solve_failed"
    )


async def _attempt_auto(page: Any, captcha_type: str) -> bool:
    """One-shot auto-solve attempt. Returns True on success, False otherwise."""
    if captcha_type == "slide":
        return await slide_solver.solve_slide(page)

    if captcha_type == "ocr":
        image_bytes = await _extract_captcha_image(page)
        if not image_bytes:
            return False
        text = await ocr_solver.solve_ocr(image_bytes)
        if not text:
            return False
        await _fill_captcha_text(page, text)
        return True

    # 未知类型不自动解 (走人工分支本已拦截, 此处兜底)。
    return False


async def _request_manual(ctx: Any, captcha_type: str) -> None:
    """Pause and request manual solve. No-ctx (unit tests) → just log, no IPC IO."""
    if ctx is None:
        logger.warning(f"Manual captcha required (no ctx): {captcha_type}")
        return
    account_id = getattr(ctx, "account_id", None) if ctx else None
    await manual_handler.request_manual_solve(ctx, captcha_type, account_id or 0)


# ── Internal helpers ──


async def _detect_captcha_type(page: Any) -> str | None:
    """Detect captcha type by scanning the page for known patterns.

    Returns: "slide" | "ocr" | "click" | "sms" | None
    """
    try:
        # Check for slide captcha indicators
        slide_selectors = [
            "captcha-verify-container",
            ".captcha--slider",
            ".geetest_panel",
            '[class*="slide"]',
        ]
        for sel in slide_selectors:
            el = await page.query_selector(sel)
            if el:
                return "slide"

        # Check for OCR/text captcha indicators
        ocr_selectors = [
            "captcha_image",
            ".captcha-img",
            "img[src*='captcha']",
        ]
        for sel in ocr_selectors:
            el = await page.query_selector(sel)
            if el:
                return "ocr"

        # Check for click captcha
        click_selectors = [
            ".geetest_panel_click",
            '[class*="click-verify"]',
        ]
        for sel in click_selectors:
            el = await page.query_selector(sel)
            if el:
                return "click"

        # Check for SMS captcha
        sms_selectors = [
            "input[name='sms_code']",
            '[class*="sms-verify"]',
        ]
        for sel in sms_selectors:
            el = await page.query_selector(sel)
            if el:
                return "sms"

    except Exception:
        # Detection failures should not crash the worker
        pass

    return None


async def _extract_captcha_image(page: Any) -> bytes | None:
    """Extract captcha image bytes from the page."""
    try:
        img_el = await page.query_selector("img[src*='captcha'], .captcha-img")
        if not img_el:
            return None
        # Get image as screenshot of the element
        return await img_el.screenshot(type="png")
    except Exception:
        return None


async def _fill_captcha_text(page: Any, text: str) -> None:
    """Fill the captcha text input."""
    try:
        input_el = await page.query_selector("input[name='captcha'], .captcha-input")
        if input_el:
            await input_el.fill(text)
    except Exception:
        pass
