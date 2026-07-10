"""CDP: launch real Chrome + connect_over_cdp + port discovery.

Design: docs/skim_design.md §4.1.
Hard constraint: Chrome args ONLY --remote-debugging-port + --user-data-dir.
playwright is lazy-imported inside attach().
"""
from __future__ import annotations

import socket
import subprocess

from subprocess import DEVNULL

import config

# PRD §8.1 场景 1.2 — surfaced to the UI (via the heartbeat watchdog reaping
# the zombie running task → paused + WS「引擎异常中断」) when the worker
# cannot attach to Chrome because the debugging port is taken or connection
# is refused. Distinct, message-carrying exception so worker_main can log the
# exact user-facing hint.
CDP_PORT_BUSY_HINT = "Chrome 调试端口被占用，请关闭所有 Chrome 窗口后重试"


class CDPAttachError(Exception):
    """Raised when connect_over_cdp fails (port busy / Chrome unreachable).

    Carries the PRD §8.1 场景 1.2 user-facing hint so worker_main can surface it.
    """

    def __init__(self, message: str = CDP_PORT_BUSY_HINT) -> None:
        super().__init__(message)
        self.fix_hint = message


def launch_real_chrome(profile_dir: str, port: int) -> subprocess.Popen:
    """Launch system Chrome with ONLY remote-debugging-port + user-data-dir."""
    chrome = config.CHROME_BIN
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
    ]
    return subprocess.Popen(args, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)


async def attach(port: int) -> "tuple":
    """Connect over CDP and return (Browser, BrowserContext).

    Lazy import playwright so this module is importable without it installed.
    Raises CDPAttachError (PRD §8.1 场景 1.2) when connect_over_cdp fails — e.g.
    the debugging port is occupied by another Chrome instance or Chrome did
    not come up in time — so worker_main can surface the user-facing hint.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception as exc:
        # Connection refused / port busy / bad endpoint — classify as a
        # user-actionable CDP attach failure (PRD §8.1 场景 1.2).
        raise CDPAttachError(f"{CDP_PORT_BUSY_HINT} ({exc})") from exc
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    return browser, ctx


def find_free_port() -> int:
    """Find a free CDP port in CDP_PORT_RANGE, incrementing past conflicts.

    Distinguish:
    - Own old worker occupying a port (reuse if profile matches).
    - Another program occupying a port (skip).
    """
    lo, hi = config.CDP_PORT_RANGE
    for port in range(lo, hi + 1):
        if _is_port_free(port):
            return port
        # Port is occupied — check if it's our own Chrome worker
        if _is_own_chrome(port):
            return port
        # Another program is using it, try next
    # All ports in range occupied — return next free after range
    port = hi + 1
    while not _is_port_free(port):
        port += 1
    return port


def _is_port_free(port: int) -> bool:
    """True if no process is listening on this port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_own_chrome(port: int) -> bool:
    """Heuristic: check if the occupant is our Chrome (by cmdline).

    Returns True if a Chrome process with --user-data-dir pointing to
    a collection profile is listening on this port.
    """
    try:
        result = subprocess.run(
            ["lsof", "-i", f"TCP:{port}", "-P", "-n"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "Google Chrome" in line or "chromium" in line.lower():
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return False
