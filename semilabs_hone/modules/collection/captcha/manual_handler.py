"""Manual captcha handler.

When automatic solving fails, pause the task and notify the user to complete
the captcha manually in the real Chrome window.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

import config


async def request_manual_solve(ctx: object, captcha_type: str, account_id: int) -> None:
    """Pause the current task and request manual captcha completion.

    Writes:
    - IPC result with status="paused" and ws_events containing "captcha_required".
    - The ws_events list is read by the web client for WebSocket broadcast.

    Args:
        ctx: IPC context object (should have request_id and IPC write methods).
        captcha_type: Type of captcha requiring manual solve.
        account_id: The account ID for this task.
    """
    message = "请切换到'小红书' Chrome 窗口完成验证"
    logger.warning(f"Manual captcha required: {captcha_type} — {message}")

    # Build the ws_events list for the web client to broadcast
    ws_event = {
        "type": "captcha_required",
        "module": "collection",
        "account_id": account_id,
        "message": message,
        "severity": "warn",
        "category": "captcha",
        "data": {"captcha_type": captcha_type, "account_id": account_id},
        "timestamp": time.time(),
    }

    # If ctx has request_id, write the paused IPC result
    request_id = getattr(ctx, "request_id", None) if ctx else None
    if request_id:
        result_path = Path(config.IPC_RESULTS) / f"{request_id}.json"
        result_data = {
            "request_id": request_id,
            "status": "paused",
            "data": {"captcha_type": captcha_type, "account_id": account_id},
            "error": None,
            "ws_events": [ws_event],
            "completed_at": time.time(),
        }

        # Atomic write: .tmp -> rename
        tmp_path = result_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(result_data, ensure_ascii=False), encoding="utf-8")
        tmp_path.rename(result_path)
        logger.info(f"Paused IPC result written: {result_path}")
    else:
        logger.info(f"Captcha required (no request_id): {captcha_type}, ws_event queued")
