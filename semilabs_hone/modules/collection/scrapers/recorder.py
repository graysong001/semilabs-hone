"""CDP recorder: captures step chains + data XHR samples (skim_design.md S8.3).

Lazy-imports playwright (recorder uses CDP via connect_over_cdp).
Module is importable even when playwright is not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """One recorded step in a flow's step chain."""

    type: str  # navigate | input | click | scroll
    url: str | None = None
    text: str | None = None
    locator: dict[str, Any] | None = None  # multi-strategy selector dict
    timestamp: float = 0.0
    # Heuristic annotation
    triggered_xhr: str | None = None  # save_as key of XHR triggered by this step


@dataclass
class XhrSample:
    """A captured XHR response sample."""

    url: str
    method: str = "GET"
    status: int = 200
    body: dict | None = None
    content_length: int = 0
    timestamp: float = 0.0


@dataclass
class RecordingResult:
    """Final result of a recording session."""

    flows: dict[str, list[Step]] = field(default_factory=dict)
    xhr_samples: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RecordingSession
# ---------------------------------------------------------------------------


class RecordingSession:
    """Records user interactions in a real Chrome via CDP.

    Captures navigate/input/click/scroll as steps with multi-strategy
    selectors. Records XHR responses with heuristic annotation (large JSON
    arriving shortly after an operation = data XHR).
    """

    def __init__(self) -> None:
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._steps: list[Step] = []
        self._xhr_samples: dict[str, XhrSample] = {}
        self._last_action_time: float = 0.0
        self._xhr_threshold_ms: int = 3000  # XHR within 3s of action = related
        self._json_size_threshold: int = 500  # bytes, heuristic for "data XHR"
        self._response_handler: Any = None
        self._pw: Any = None  # playwright instance
        self._cdp_port: int = 9333
        self._chrome_proc: Any = None

    async def start(self, base_url: str) -> None:
        """Open Chrome and inject CDP listeners.

        Uses lazy playwright import so the module is importable without it.
        """
        playwright = _get_playwright()
        pw = await playwright.async_playwright().start()
        self._pw = pw

        browser, context, cdp_port = await _launch_chrome_and_attach(pw, base_url)
        self._browser = browser
        self._context = context
        self._cdp_port = cdp_port

        pages = context.pages if hasattr(context, "pages") else []
        self._page = pages[0] if pages else await context.new_page()

        # Navigate to base URL
        try:
            await self._page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            logger.warning("Initial goto failed (page may not be ready): %s", e)

        # Record navigate step
        step = Step(
            type="navigate",
            url=base_url,
            timestamp=time.time(),
        )
        self._steps.append(step)
        self._last_action_time = step.timestamp

        # Listen for network responses
        self._response_handler = self._on_response
        self._page.on("response", self._response_handler)

        logger.info("RecordingSession started for %s (CDP port %d)", base_url, cdp_port)

    async def stop(self) -> RecordingResult:
        """Stop recording and return results.

        Groups steps into flows heuristically and collects XHR samples.
        """
        # Remove response listener
        if self._page and self._response_handler:
            try:
                self._page.remove_listener("response", self._response_handler)
            except Exception:
                pass

        # Heuristic: group steps into flows
        result = self._build_recording_result()

        # Cleanup browser if we launched it
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
            except Exception:
                pass

        logger.info("RecordingSession stopped: %d flows, %d xhr samples",
                     len(result.flows), len(result.xhr_samples))
        return result

    def record_input(self, text: str, selector: dict[str, Any] | None = None) -> None:
        """Record an input step (called by UI step editor or CDP handler)."""
        step = Step(
            type="input",
            text=text,
            locator=selector or {},
            timestamp=time.time(),
        )
        self._steps.append(step)
        self._last_action_time = step.timestamp

    def record_click(self, selector: dict[str, Any] | None = None) -> None:
        """Record a click step."""
        step = Step(
            type="click",
            locator=selector or {},
            timestamp=time.time(),
        )
        self._steps.append(step)
        self._last_action_time = step.timestamp

    def record_scroll(self) -> None:
        """Record a scroll step."""
        step = Step(
            type="scroll",
            timestamp=time.time(),
        )
        self._steps.append(step)
        self._last_action_time = step.timestamp

    def record_navigate(self, url: str) -> None:
        """Record a navigate step."""
        step = Step(
            type="navigate",
            url=url,
            timestamp=time.time(),
        )
        self._steps.append(step)
        self._last_action_time = step.timestamp

    async def capture_element_selectors(self, element: Any) -> dict[str, Any]:
        """Extract multi-strategy selectors from a page element.

        Returns dict with text/role/aria_label/nth for runtime fallback.
        """
        selectors: dict[str, Any] = {}
        try:
            # text content
            text = await element.inner_text(timeout=1000)
            if text:
                selectors["text"] = text.strip()[:100]

            # role
            role = await element.evaluate("el => el.getAttribute('role')")
            if role:
                selectors["role"] = role

            # aria-label
            aria = await element.evaluate("el => el.getAttribute('aria-label')")
            if aria:
                selectors["aria_label"] = aria

            # nth-of-type fallback via evaluate
            nth = await element.evaluate("""el => {
                const parent = el.parentElement;
                if (!parent) return null;
                const tag = el.tagName.toLowerCase();
                const siblings = Array.from(parent.querySelectorAll(tag));
                return siblings.indexOf(el);
            }""")
            if nth is not None and nth >= 0:
                selectors["nth"] = nth

            # tag as last resort
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            if tag:
                selectors["css"] = tag

        except Exception as e:
            logger.debug("Could not extract element selectors: %s", e)

        return selectors

    def _on_response(self, response: Any) -> None:
        """Handle network response events. Heuristically marks data XHR."""
        try:
            url = response.url if hasattr(response, "url") else ""
            method = "GET"
            if hasattr(response, "request") and hasattr(response.request, "method"):
                method = response.request.method

            # Only care about XHR/fetch, not static assets
            content_type = ""
            try:
                headers = response.headers if hasattr(response, "headers") else {}
                if hasattr(headers, "get"):
                    content_type = headers.get("content-type", "")
                elif isinstance(headers, dict):
                    content_type = headers.get("content-type", "")
            except Exception:
                pass

            is_json = "json" in content_type.lower() if content_type else False
            is_xhr = "xhr" in (getattr(response.request, "resource_type", "") if hasattr(response, "request") else "").lower()
            is_fetch = "fetch" in (getattr(response.request, "resource_type", "") if hasattr(response, "request") else "").lower()

            if not (is_json or is_xhr or is_fetch):
                return

            status = response.status if hasattr(response, "status") else 0
            ts = time.time()
            now = ts

            # Heuristic: large JSON arriving shortly after an action = data XHR
            is_data_xhr = False
            time_since_action = (now - self._last_action_time) * 1000  # ms

            if is_json and time_since_action < self._xhr_threshold_ms:
                try:
                    body = response.json() if hasattr(response, "json") else None
                    if body:
                        content_len = len(json.dumps(body, ensure_ascii=False))
                        if content_len > self._json_size_threshold:
                            is_data_xhr = True
                except Exception:
                    pass

            # Try to get body
            body = None
            content_len = 0
            try:
                if hasattr(response, "json"):
                    body = response.json()
                    content_len = len(json.dumps(body, ensure_ascii=False))
                elif hasattr(response, "text"):
                    text = response.text()
                    if asyncio.iscoroutine(text):
                        import asyncio as _asyncio
                        try:
                            loop = _asyncio.get_running_loop()
                            text = loop.run_until_complete(text)
                        except Exception:
                            text = None
                    if text:
                        body = json.loads(text)
                        content_len = len(text)
            except Exception:
                pass

            sample = XhrSample(
                url=url,
                method=method,
                status=status,
                body=body,
                content_length=content_len,
                timestamp=ts,
            )

            # Key by URL for dedup; keep largest sample
            existing = self._xhr_samples.get(url)
            if existing is None or content_len > existing.content_length:
                self._xhr_samples[url] = sample

            # Annotate last step if this looks like a data XHR
            if is_data_xhr and self._steps:
                last = self._steps[-1]
                save_as = _make_save_as_name(url, method)
                last.triggered_xhr = save_as

        except Exception as e:
            logger.debug("_on_response error: %s", e)

    def _build_recording_result(self) -> RecordingResult:
        """Build RecordingResult from recorded steps and XHR samples."""
        result = RecordingResult()

        # Heuristic: group steps into flows
        # First navigate = search flow (default), subsequent navigations may
        # start new flows. Simple heuristic: one flow unless user marks otherwise.
        flow_name = "search"
        current_flow: list[Step] = []

        for step in self._steps:
            if step.type == "navigate" and current_flow and len(current_flow) >= 2:
                # New navigate after steps = likely new flow
                if current_flow:
                    result.flows[flow_name] = current_flow
                flow_name = _guess_flow_name(step.url or "")
                current_flow = []
            current_flow.append(step)

        if current_flow:
            result.flows[flow_name] = current_flow

        # XHR samples -> dict keyed by save_as name
        for url, sample in self._xhr_samples.items():
            save_as = _make_save_as_name(url, sample.method)
            result.xhr_samples[save_as] = {
                "url": sample.url,
                "method": sample.method,
                "status": sample.status,
                "body": sample.body,
                "content_length": sample.content_length,
                "timestamp": sample.timestamp,
            }

        return result


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------


async def record_platform(display_name: str, base_url: str) -> RecordingResult:
    """Convenience: start recording, let user interact, stop and return result.

    In practice, the UI step editor calls session.record_*() methods between
    start() and stop(). This function provides a simple start-stop wrapper.
    """
    session = RecordingSession()
    await session.start(base_url)
    # In real usage: user interacts with Chrome, UI calls record_*() methods.
    # For the basic wrapper, we just return after navigation.
    result = await session.stop()
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_playwright() -> Any:
    """Lazy import playwright. Raises ImportError if not installed."""
    try:
        import playwright
        return playwright
    except ImportError:
        raise ImportError(
            "playwright is required for recorder. Install with: pip install playwright"
        )


async def _launch_chrome_and_attach(
    pw: Any,
    base_url: str,
) -> tuple[Any, Any, int]:
    """Launch Chrome via subprocess and attach via connect_over_cdp.

    Follows PROJECT_CONTEXT.md S3 constraints: no automation flags.
    """
    import subprocess
    from pathlib import Path

    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    # Find free port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    cdp_port = sock.getsockname()[1]
    sock.close()

    profile_dir = str(Path.home() / ".semilabs" / "recorder_profile")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    args = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
    ]

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    # Wait for Chrome to be ready
    await asyncio.sleep(1.5)

    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
    context = browser.contexts[0] if browser.contexts else await browser.new_context()

    return browser, context, cdp_port


def _make_save_as_name(url: str, method: str) -> str:
    """Create a save_as identifier from URL + method.

    E.g. /api/sns/web/v1/search/notes + POST -> search_notes
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    # Take last 2 meaningful parts
    name = "_".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "resp")
    # Sanitize
    name = "".join(c if c.isalnum() else "_" for c in name)
    return name


def _guess_flow_name(url: str) -> str:
    """Guess a flow name from a URL."""
    lower = url.lower()
    if "search" in lower or "query" in lower:
        return "search"
    if "comment" in lower:
        return "comments"
    if "detail" in lower or "feed" in lower or "note" in lower or "post" in lower:
        return "detail"
    return f"flow_{len(url) % 100}"
