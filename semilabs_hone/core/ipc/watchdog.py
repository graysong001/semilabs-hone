"""Web-side heartbeat watchdog — reap zombie running tasks.

PRD §3.3: the worker writes progress/heartbeat.json every ~10s while alive.
If the worker process is killed / crashes / loses power, the heartbeat stops.
The web side polls it; when stale (> STALE_THRESHOLD, default 30s) it flips
any DB `running` task to `paused` and broadcasts a WS event so the UI shows
「引擎异常中断」instead of hanging on a dead Running task forever.

Read-only on progress/ (contract §7.2: web only reads progress/); writes go
to factory.db, which the web process owns.

Functions:
    check_heartbeat(now) -> (is_stale, age_or_none, message)
    reap_stale_running_task(session, now, threshold) -> ws_event | None
    async run_heartbeat_watchdog(interval, stale_threshold)  # background loop
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger

from .paths import heartbeat_age

# A heartbeat older than this (seconds) means the worker is presumed dead.
STALE_THRESHOLD = 30.0


def check_heartbeat(now: float | None = None, threshold: float = STALE_THRESHOLD):
    """Inspect the heartbeat freshness.

    Returns (is_stale, age, message):
      - is_stale: True when no heartbeat exists OR age > threshold.
      - age: seconds since last heartbeat, or None when no heartbeat at all.
      - message: human-readable hint for WS/UI.
    """
    age = heartbeat_age(now=now)
    if age is None:
        return True, None, "引擎未启动或已退出"
    if age > threshold:
        return True, age, "引擎异常中断"
    return False, age, "alive"


def reap_stale_running_task(session, now: float | None = None, threshold: float = STALE_THRESHOLD):
    """Flip a stale `running` task to `paused`; return a WS event or None.

    Scans DB for tasks with status='running'; for the first one whose
    heartbeat is stale, sets status='paused' + error_message and returns a
    ProgressMessage-shaped dict for ws_manager.broadcast. Returns None when
    no stale running task is found.
    """
    from semilabs_hone.core.models.task import ScrapeTask

    is_stale, age, message = check_heartbeat(now=now, threshold=threshold)
    if not is_stale:
        return None

    task = session.query(ScrapeTask).filter(ScrapeTask.status == "running").first()
    if task is None:
        return None

    task.status = "paused"
    task.error_message = message
    task.error_category = "HeartbeatStale"
    session.commit()

    event = {
        "type": "error",
        "module": "collection",
        "task_id": task.id,
        "message": message,
    }
    logger.warning(f"[watchdog] task {task.id} running→paused (heartbeat stale, age={age})")
    return event


async def run_heartbeat_watchdog(
    interval: float = 15.0,
    stale_threshold: float = STALE_THRESHOLD,
) -> None:
    """Background loop: poll heartbeat, reap stale running tasks, broadcast.

    Launched as an asyncio task on app startup. Runs until cancelled.
    """
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.ui.ws import ws_manager

    while True:
        try:
            sess = get_session()
            try:
                event = reap_stale_running_task(sess, threshold=stale_threshold)
            finally:
                sess.close()
            if event is not None:
                await ws_manager.broadcast(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[watchdog] poll failed: {exc}")
        await asyncio.sleep(interval)
