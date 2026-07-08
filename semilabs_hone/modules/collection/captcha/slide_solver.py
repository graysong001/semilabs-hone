"""Slide captcha solver.

Uses OpenCV for gap detection + DM-06 generate_slide_track for physical track.
Lazy import cv2 so this module is importable without OpenCV installed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


async def solve_slide(page: Any) -> bool:
    """Solve slide captcha: detect gap with OpenCV, drag with physical track.

    Args:
        page: Playwright Page object with slide captcha visible.

    Returns:
        True if captcha was solved, False otherwise.
    """
    # Lazy import heavy dependencies
    try:
        import cv2
    except ImportError:
        logger.warning("OpenCV not available, slide captcha cannot be solved automatically")
        return False

    try:
        import numpy as np
    except ImportError:
        logger.warning("NumPy not available, slide captcha cannot be solved automatically")
        return False

    try:
        from playwright.async_api import Page
    except ImportError:
        logger.warning("Playwright not available")
        return False

    try:
        # Locate the slider button and the background image
        slider_btn = await page.query_selector(".geetest_slider_button, .captcha-slider, [class*='slider-btn']")
        bg_img = await page.query_selector(".geetest_canvas_bg, .captcha-bg, img[src*='bg']")
        full_img = await page.query_selector(".geetest_canvas_fullbg, .captcha-fullbg")

        if not slider_btn or not bg_img:
            logger.warning("Slide captcha elements not found")
            return False

        # Get background and full images as screenshots
        bg_bytes = await bg_img.screenshot(type="png")
        full_bytes = await full_img.screenshot(type="png") if full_img else bg_bytes

        # Decode images
        bg_arr = np.frombuffer(bg_bytes, np.uint8)
        full_arr = np.frombuffer(full_bytes, np.uint8)
        bg_cv = cv2.imdecode(bg_arr, cv2.IMREAD_GRAYSCALE)
        full_cv = cv2.imdecode(full_arr, cv2.IMREAD_GRAYSCALE)

        if bg_cv is None or full_cv is None:
            logger.warning("Failed to decode captcha images")
            return False

        # Ensure same dimensions for comparison
        if bg_cv.shape != full_cv.shape:
            full_cv = cv2.resize(full_cv, (bg_cv.shape[1], bg_cv.shape[0]))

        # Find the gap by comparing images
        diff = cv2.absdiff(full_cv, bg_cv)
        _, thresh = cv2.threshold(diff, 50, 255, cv2.THRESH_BINARY)

        # Find contours to locate the gap
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            # Fallback: try template matching on the slider piece
            piece_img = await page.query_selector(".geetest_canvas_slice, .captcha-piece")
            if piece_img:
                piece_bytes = await piece_img.screenshot(type="png")
                piece_arr = np.frombuffer(piece_bytes, np.uint8)
                piece_cv = cv2.imdecode(piece_arr, cv2.IMREAD_GRAYSCALE)
                if piece_cv is not None:
                    result = cv2.matchTemplate(bg_cv, piece_cv, cv2.TM_CCOEFF_NORMED)
                    _, _, _, max_loc = cv2.minMaxLoc(result)
                    distance = max_loc[0]
                else:
                    return False
            else:
                logger.warning("No contours found for gap detection")
                return False
        else:
            # Use the leftmost contour (the gap)
            gap_contour = min(contours, key=cv2.boundingRect)
            x, y, w, h = cv2.boundingRect(gap_contour)
            distance = x

        logger.info(f"Slide gap detected at distance={distance}")

        # Generate physical track using DM-06 generate_slide_track
        from semilabs_hone.modules.collection.anti_detect.human_behavior import (
            generate_slide_track,
        )
        track = generate_slide_track(distance)

        # Perform the slide: click and drag
        await _execute_slide(page, slider_btn, track)

        # Wait a moment for verification
        await asyncio.sleep(1)
        return True

    except Exception as e:
        logger.error(f"Slide captcha solve error: {e}")
        return False


async def _execute_slide(page: Any, slider_btn: Any, track: list[dict]) -> None:
    """Execute the slide drag using the generated track.

    Args:
        page: Playwright Page.
        slider_btn: The slider button element.
        track: List of {x, y, t} from generate_slide_track.
    """
    box = await slider_btn.bounding_box()
    if not box:
        await slider_btn.click()
        return

    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2

    # Click and hold the slider
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()

    # Follow the track
    last_t = 0.0
    for point in track:
        px = start_x + point["x"]
        py = start_y + point["y"]
        await page.mouse.move(px, py)
        delay = (point["t"] - last_t) / 1000.0 if point["t"] > last_t else 0.01
        if delay > 0:
            await asyncio.sleep(min(delay, 0.05))
        last_t = point["t"]

    # Release
    await asyncio.sleep(0.1)
    await page.mouse.up()
