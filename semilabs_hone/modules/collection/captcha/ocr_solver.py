"""OCR captcha solver.

Uses ddddocr for text recognition.
Lazy import so this module is importable without ddddocr installed.
"""
from __future__ import annotations

from loguru import logger


async def solve_ocr(image_bytes: bytes) -> str:
    """Recognize text from captcha image using ddddocr.

    Args:
        image_bytes: Captcha image bytes (PNG/JPG).

    Returns:
        Recognized text string, or empty string on failure.
    """
    try:
        import ddddocr
    except ImportError:
        logger.warning("ddddocr not available, OCR captcha cannot be solved automatically")
        return ""

    try:
        ocr = ddddocr.DdddOcr(show_ad=False)
        result = ocr.classification(image_bytes)
        text = result.strip() if result else ""
        if text:
            logger.debug(f"OCR recognized: {text}")
        return text
    except Exception as e:
        logger.error(f"OCR recognition error: {e}")
        return ""
