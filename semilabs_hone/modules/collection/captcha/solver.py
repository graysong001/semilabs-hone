"""Captcha detection + dispatcher.

Detect captcha type on page -> dispatch to slide/ocr/manual solver.
Core principle: auto-solve fails once then pauses.
Design: docs/skim_design.md §10
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from semilabs_hone.core.utils.retry import CaptchaError

if TYPE_CHECKING:
    from playwright.async_api import Page


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


async def detect_and_solve(page: Any, ctx: Any = None) -> SolveResult:
    """Detect captcha on the current page and attempt to solve.

    Detection heuristics:
    - Slide captcha: presence of slider track element or "slide" keywords.
    - OCR/text captcha: presence of captcha image with text input field.
    - Click/SMS: presence of grid click or SMS code input.

    Args:
        page: Playwright Page object.
        ctx: Optional IPC context for manual fallback.

    Returns:
        SolveResult with status: solved | paused | failed.
    """
    from semilabs_hone.modules.collection.captcha.manual_handler import (
        request_manual_solve,
    )
    from semilabs_hone.modules.collection.captcha.ocr_solver import solve_ocr
    from semilabs_hone.modules.collection.captcha.slide_solver import solve_slide

    captcha_type = await _detect_captcha_type(page)
    if captcha_type is None:
        logger.debug("No captcha detected")
        return SolveResult(status="solved")

    logger.info(f"Captcha detected: {captcha_type}")

    try:
        if captcha_type == "slide":
            success = await solve_slide(page)
            if success:
                logger.info("Slide captcha solved successfully")
                return SolveResult(status="solved", captcha_type="slide")
            logger.warning("Slide captcha solve failed")
            return SolveResult(status="failed", captcha_type="slide")

        elif captcha_type == "ocr":
            image_bytes = await _extract_captcha_image(page)
            if image_bytes:
                text = await solve_ocr(image_bytes)
                if text:
                    await _fill_captcha_text(page, text)
                    logger.info(f"OCR captcha solved: {text}")
                    return SolveResult(status="solved", captcha_type="ocr")
            logger.warning("OCR captcha solve failed")
            return SolveResult(status="failed", captcha_type="ocr")

        else:
            # click, sms, or unknown -> manual
            logger.info(f"Manual solve required for: {captcha_type}")
            account_id = getattr(ctx, "account_id", None) if ctx else None
            await request_manual_solve(ctx, captcha_type, account_id or 0)
            return SolveResult(status="paused", captcha_type=captcha_type)

    except CaptchaError:
        raise
    except Exception as e:
        logger.error(f"Captcha solve error: {e}")
        return SolveResult(status="failed", captcha_type=captcha_type, error=str(e))


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
