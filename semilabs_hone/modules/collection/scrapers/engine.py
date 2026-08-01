"""GenericEngine — platform-agnostic step-chain replay + JSONPath extraction + light LLM fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

from pydantic import ValidationError

from semilabs_hone.core.models.schemas import ItemRef, ScrapedPost, ScrapedComment
from semilabs_hone.modules.collection.scrapers.base import (
    GROUP_COMMENTS,
    GROUP_ITEM_REF,
    GROUP_POST_BODY,
    GROUP_POST_INTERACTIONS,
    BasePlatformScraper,
)
from semilabs_hone.modules.collection.scrapers.field_extract import (
    extract_api,
    extract_dom,
    extract_dom_multi,
    render_template,
)
from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec

logger = logging.getLogger(__name__)

#: How often the step loop checks the interception buffer.
_XHR_POLL_INTERVAL = 0.05
#: Keep the buffer bounded on long-running sessions.
_XHR_BUFFER_LIMIT = 200
#: Extra window a scroll_collect step waits for the next page's XHR after a
#: wheel scroll (the human-scroll wait_ms usually covers the latency; tests
#: zero these against static-snapshot mocks instead of sleeping real seconds).
_SNAPSHOT_WAIT_S = 1.5
_SNAPSHOT_POLL_S = 0.1


# Map group strings to their Pydantic models
_GROUP_MODEL_MAP = {
    GROUP_ITEM_REF: ItemRef,
    GROUP_POST_BODY: ScrapedPost,
    GROUP_POST_INTERACTIONS: ScrapedPost,
    GROUP_COMMENTS: ScrapedComment,
}


class RiskProbeHit(Exception):
    """Raised inside run_flow when the on_risk probe callback reports a hit.

    PRD §4.4.1/§4.4.2: after every goto/scroll/click the worker must run a risk
    probe; on hit it must immediately break the scrape loop and surface
    need_human. The engine fires probes at the action sites and raises this so
    the handler can translate it into a need_human status + resume wait.
    """

    def __init__(self, hit: Any = None, msg: str = "") -> None:
        self.hit = hit
        super().__init__(msg or getattr(hit, "kind", None) or "risk_probe_hit")


class GenericEngine(BasePlatformScraper):
    """Replay step chains from platform.yaml, intercept XHR, extract fields.

    Pure JSONPath at runtime; LLM fallback only on validation failure.
    """

    def __init__(
        self,
        spec: PlatformSpec,
        ctx: Any = None,
        account: Any = None,
    ) -> None:
        self.spec = spec
        self.ctx = ctx
        self.account = account
        self.page: Any = None
        self._llm_fail_count = 0
        self._llm_fail_threshold = 3
        self._fp_applied = False
        self._armed_page: Any = None
        self._xhr_buffer: list[dict] = []
        # Optional risk-probe callback set by the handler: async (page) -> hit|None.
        # When set, run_flow fires it after navigate/scroll/scroll_collect/click
        # and raises RiskProbeHit on a hit (PRD §4.4.1).
        self.on_risk: Callable[[Any], Awaitable[Any]] | None = None

    async def ensure_page(self) -> Any:
        """Return the working page, creating it from the context on first use.

        Public because callers outside a flow need the same page the flow
        will use (e.g. the warmup browse before scraping, design Layer 4).
        Arms XHR interception on acquisition: a page's own load-time requests
        fire before any step runs (USER_SOP G24).
        """
        if self.page is None and self.ctx is not None:
            try:
                pages = self.ctx.pages if hasattr(self.ctx, "pages") else []
                self.page = pages[0] if pages else await self.ctx.new_page()
            except Exception:
                pass
        if self.page is None:
            raise RuntimeError("No page available; provide a context or set engine.page")
        await self._apply_fingerprint_once()
        self._arm_interception(self.page)
        return self.page

    async def _apply_fingerprint_once(self) -> None:
        """Apply the account's fixed fingerprint via CDP Emulation (once per page).

        Layer 2 wiring (FIX_PLAN F6): no-op without an account; failures are
        logged and swallowed so scraping is never blocked by fingerprinting.
        """
        if self._fp_applied or self.account is None:
            return
        self._fp_applied = True
        try:
            from semilabs_hone.modules.collection.anti_detect.fingerprint import (
                apply_to_page,
                load_fingerprint,
            )
            await apply_to_page(self.page, load_fingerprint(self.account))
        except Exception as exc:
            logger.warning("fingerprint apply failed (continuing without): %s", exc)

    async def run_flow(self, flow_name: str, **vars: Any) -> list:
        """Replay a flow's step chain and return extracted items."""
        flow = self.spec.flows.get(flow_name)
        if not flow:
            logger.warning("Flow '%s' not found in spec '%s'", flow_name, self.spec.platform)
            return []

        saved: dict[str, Any] = {}
        out: list = []
        page = await self.ensure_page()

        for step in flow.steps:
            match step.type:
                case "navigate":
                    # A navigation invalidates everything captured so far:
                    # leftovers from the previous page would otherwise be
                    # consumed by this page's wait_xhr (USER_SOP G24).
                    self._xhr_buffer.clear()
                    await page.goto(self._absolute_url(step.url or "", **vars))
                    await self._probe(page)

                case "input":
                    locator = step.locator
                    text_val = render_template(step.text or "", **vars)
                    await self._human_input(page, locator, text_val)

                case "click":
                    await self._human_click(page, step.locator)
                    await self._probe(page)

                case "scroll":
                    await self._random_scroll(page, step.max_times, step.wait_ms)
                    await self._probe(page)

                case "scroll_collect":
                    # Bounded incremental list collection (PRD §8.4 场景4.2):
                    # re-extract after each wheel scroll, dedup new item ids, stop
                    # at max_scrolls (20) or empty_break (5) consecutive no-new.
                    await self._scroll_collect(page, step, saved, out)

                case "go_back":
                    # Return from a detail page to the list (PRD §4.2.2 step 5).
                    try:
                        await page.go_back()
                    except Exception as exc:
                        logger.warning("page.go_back() failed: %s", exc)
                    await self._probe(page)

                case "wait_xhr":
                    resp_data = await self._wait_xhr(
                        page,
                        step.url_pattern or "",
                        step.method,
                        step.timeout_ms,
                        ssr_state_key=getattr(step, "ssr_state_key", None),
                        ssr_state_path=getattr(step, "ssr_state_path", None),
                    )
                    if step.save_as:
                        saved[step.save_as] = resp_data

                case "extract":
                    resp = saved.get(step.from_ or "")
                    if step.group and step.map:
                        items = extract_api(resp, step.group, step.map) if resp else []
                        if not items:
                            # Layer 5 (FIX_PLAN F10): XHR interception first;
                            # empty/timed-out capture falls back to DOM extraction.
                            items = await self._extract_dom_fallback(page, step.group, step.map)
                        validated = await self._validate_group(items, step.group)
                        out.extend(validated)

                case "wait_selector":
                    if step.selector:
                        try:
                            await page.wait_for_selector(step.selector, timeout=5000)
                        except Exception:
                            logger.warning("Selector not found: %s", step.selector)

        return out

    def _absolute_url(self, template: str, **vars: Any) -> str:
        """Render a step URL template and resolve it against the platform base.

        Recorded flows store site-relative paths ("/search?kw={keyword}");
        a browser cannot navigate to those, so they are joined with
        ``spec.base_url`` here — the single place navigation happens (G22).
        """
        rendered = render_template(template, **vars)
        return urljoin(self.spec.base_url, rendered)

    async def _extract_dom_fallback(self, page: Any, group: str, field_map: dict) -> list[dict]:
        """DOM extraction fallback for an empty XHR capture (Layer 5).

        Only selector maps (``css:``/``xpath:``) can be read from the DOM;
        a JSONPath-only map has nothing to offer here and must not fabricate
        an all-None row (USER_SOP G26). extract_dom is a pure HTML->dicts
        function; the page IO lives here so the extractor stays sync.
        """
        if not any(str(expr).startswith(("css:", "xpath:")) for expr in field_map.values()):
            logger.warning(
                "No DOM selectors for group '%s'; cannot fall back (re-record the flow)",
                group,
            )
            return []
        try:
            html = await page.content()
        except Exception as exc:
            logger.warning("DOM fallback: page.content() failed: %s", exc)
            return []
        if not isinstance(html, str) or not html:
            return []
        return extract_dom(html, group, field_map)

    async def _probe(self, page: Any) -> None:
        """Fire the risk probe if wired; raise RiskProbeHit on a hit.

        No-op when the handler hasn't set on_risk (engine unit tests).
        """
        if self.on_risk is None:
            return
        hit = await self.on_risk(page)
        if hit is not None:
            raise RiskProbeHit(hit)

    @staticmethod
    def _item_dedup_key(item: dict) -> str:
        """Stable dedup key for an extracted item dict (item_id or platform_id)."""
        return str(item.get("item_id") or item.get("platform_id") or id(item))

    async def _scroll_collect(
        self,
        page: Any,
        step: Any,
        saved: dict[str, Any],
        out: list,
    ) -> None:
        """Scroll-and-collect loop with hard caps (PRD §8.4 场景4.2).

        Seeds from the ``from_`` snapshot, then after each wheel scroll pulls
        the NEXT intercepted response matching ``step.url_pattern``/``method``
        from the live buffer — a lazy-load list issues a fresh XHR per page,
        so re-parsing the seed snapshot after every scroll would dedup to zero
        forever (that static-snapshot loop never saw any page beyond the
        first). Dedups against already-collected ids, appends fresh validated
        items to ``out``. Stops at ``max_scrolls`` (default 20) or
        ``empty_break`` (default 5) consecutive scrolls with no new items —
        never deadlocks on an infinite loader.

        DOM fallback: when XHR extraction yields nothing, falls back to
        page DOM using ``dom_container`` + ``dom_fallback`` selectors from
        the step definition (mirrors the extract step's _extract_dom_fallback).
        """
        resp = saved.get(step.from_ or "")
        group = step.group
        fmap = step.map
        if resp is None or not group or not fmap:
            return

        collected: set[str] = set()
        consecutive_empty = 0
        scrolls = 0

        # Seed: extract the current snapshot before any scroll.
        fresh = self._extract_dedup(resp, group, fmap, collected)
        if fresh:
            out.extend(await self._validate_group(fresh, group))
            consecutive_empty = 0
        else:
            consecutive_empty = 1

        while scrolls < step.max_scrolls and consecutive_empty < step.empty_break:
            await self._random_scroll(page, 1, step.wait_ms)
            scrolls += 1
            new_resp = await self._take_new_snapshot(step)
            if new_resp is None:
                consecutive_empty += 1
                continue
            fresh = self._extract_dedup(new_resp, group, fmap, collected)
            if fresh:
                out.extend(await self._validate_group(fresh, group))
                consecutive_empty = 0
            else:
                consecutive_empty += 1

        # DOM fallback: if XHR produced nothing, extract from current page DOM.
        if not out:
            dom_items = await self._scroll_collect_dom_fallback(page, step, group, collected)
            if dom_items:
                out.extend(await self._validate_group(dom_items, group))

        logger.debug(
            "scroll_collect: scrolls=%d items=%d consecutive_empty=%d",
            scrolls, len(out), consecutive_empty,
        )

    async def _take_new_snapshot(self, step: Any) -> dict | None:
        """Pull the next buffered response for a scroll_collect step.

        The wheel scroll itself takes time (human multi-step deltas + wait_ms)
        during which the lazy-load XHR usually lands; poll a short extra
        window for stragglers. A step without ``url_pattern`` cannot receive
        incremental pages — return None immediately (same net effect as the
        old static re-parse, without the wasted work).
        """
        url_pattern = getattr(step, "url_pattern", None) or ""
        if not url_pattern:
            return None
        method = getattr(step, "method", None)
        captured = self._take_captured(url_pattern, method)
        if captured is not None:
            return captured
        deadline = time.monotonic() + _SNAPSHOT_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(_SNAPSHOT_POLL_S)
            captured = self._take_captured(url_pattern, method)
            if captured is not None:
                return captured
        return None

    async def _scroll_collect_dom_fallback(
        self,
        page: Any,
        step: Any,
        group: str,
        collected: set[str],
    ) -> list[dict]:
        """DOM extraction fallback for scroll_collect when XHR yields nothing.

        Uses ``dom_container`` (repeating card selector) and ``dom_fallback``
        (per-field CSS map) from the step's extra config.  Returns fresh items
        not already in ``collected``.
        """
        dom_container = getattr(step, "dom_container", None)
        dom_fallback = getattr(step, "dom_fallback", None)
        if not dom_container or not dom_fallback:
            logger.warning(
                "scroll_collect DOM fallback: no dom_container/dom_fallback in step; "
                "cannot fall back (add CSS selectors to platform.yaml)"
            )
            return []
        logger.info("XHR failed, falling back to DOM extraction")
        try:
            html = await page.content()
        except Exception as exc:
            logger.warning("scroll_collect DOM fallback: page.content() failed: %s", exc)
            return []
        if not isinstance(html, str) or not html:
            return []
        items = extract_dom_multi(html, dom_container, dom_fallback)
        # Dedup against already-collected IDs
        fresh: list[dict] = []
        for it in items:
            key = self._item_dedup_key(it)
            if key in collected:
                continue
            collected.add(key)
            fresh.append(it)
        return fresh

    def _extract_dedup(
        self,
        resp: Any,
        group: str,
        fmap: dict[str, str],
        collected: set[str],
    ) -> list[dict]:
        """Extract from a snapshot, returning only items whose dedup key is new
        (and registering them in ``collected``). Never raises.
        """
        try:
            items = extract_api(resp, group, fmap)
        except Exception:
            return []
        fresh: list[dict] = []
        for it in items:
            key = self._item_dedup_key(it)
            if key in collected:
                continue
            collected.add(key)
            fresh.append(it)
        return fresh

    async def _validate_group(self, items: list[dict], group: str) -> list:
        """Validate items against the Pydantic model for the group.

        Injects `platform` from spec into every group (ItemRef/Post/Comment)
        so multi-platform rows never cross-tag (FIX_PLAN F7).
        On failure: try LLM fallback for individual items.
        """
        model = _GROUP_MODEL_MAP.get(group)
        if not model:
            return items  # No model to validate against

        validated = []
        for item in items:
            # Inject platform from spec for every group
            item = {**item, "platform": self.spec.platform}
            try:
                validated.append(model(**{k: v for k, v in item.items() if k in model.model_fields}))
            except (ValidationError, TypeError):
                # LLM fallback for this single item
                llm_result = await self._llm_fallback(item, group)
                if llm_result is not None:
                    validated.append(llm_result)
                else:
                    self._llm_fail_count += 1

        return validated

    @staticmethod
    def _llm_model() -> str:
        """Lazy-read config.LLM_MODEL — single source of truth (FIX_PLAN F12)."""
        try:
            import config
            return config.LLM_MODEL
        except Exception:
            return "claude-haiku-4-5-20251001"

    async def _llm_fallback(self, item: dict, group: str):
        """Light LLM fallback — lazy import anthropic.

        Skipped when no credentials are configured: a scrape loop must not
        fire unauthenticated API calls per item (USER_SOP G27).
        """
        if self._llm_fail_count >= self._llm_fail_threshold:
            logger.warning(
                "LLM fallback threshold reached (%d); suggest re-recording flow",
                self._llm_fail_count,
            )
            return None

        import os

        if not os.getenv("ANTHROPIC_API_KEY"):
            logger.debug("LLM fallback skipped: ANTHROPIC_API_KEY not set")
            return None

        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return None

        try:
            client = AsyncAnthropic()
            schema_fields = list(_GROUP_MODEL_MAP.get(group, ItemRef).model_fields.keys())
            prompt = (
                f"Extract the following fields from this JSON data. "
                f"Return a JSON object with keys: {', '.join(schema_fields)}. "
                f"Use null for missing fields.\n\nJSON:\n{json.dumps(item, ensure_ascii=False)}"
            )
            resp = await client.messages.create(
                model=self._llm_model(),
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.content[0].text
            parsed = json.loads(content)
            model_cls = _GROUP_MODEL_MAP.get(group, ItemRef)
            return model_cls(**{k: v for k, v in parsed.items() if k in model_cls.model_fields})
        except Exception as e:
            logger.warning("LLM fallback failed: %s", e)
            return None

    async def _human_input(self, page: Any, locator: Any, text: str):
        """Simulate human-like input (delegates to anti_detect if available)."""
        try:
            from semilabs_hone.modules.collection.anti_detect.human_behavior import human_type
            await human_type(page, locator, text)
        except ImportError:
            # Fallback: direct input
            selector = self._locator_to_css(locator)
            if selector:
                await page.fill(selector, text)
            else:
                await page.keyboard.type(text)

    async def _human_click(self, page: Any, locator: Any):
        """Simulate human-like click."""
        try:
            from semilabs_hone.modules.collection.anti_detect.human_behavior import human_click
            await human_click(page, locator)
        except ImportError:
            selector = self._locator_to_css(locator)
            if selector:
                await page.click(selector)

    async def _random_scroll(self, page: Any, max_times: int, wait_ms: int):
        """Random scroll to trigger lazy loading.

        Delegates to DM-06 human_behavior.random_scroll (mouse.wheel multi-step
        + micro-pauses). PRD §4.2.1 forbids instant-teleport evaluate scrolling,
        so even the fallback path uses physical mouse.wheel deltas — never an
        evaluate-based scroll.
        """
        try:
            from semilabs_hone.modules.collection.anti_detect.human_behavior import random_scroll
            await random_scroll(page, max_times, wait_ms)
        except ImportError:
            # Fallback: physical wheel deltas (never evaluate-based scroll — redline).
            for _ in range(max(1, max_times)):
                try:
                    await page.mouse.wheel(0, 800)
                except Exception:
                    pass
                await asyncio.sleep(max(0.05, wait_ms / 1000.0))

    async def _wait_xhr(
        self,
        page: Any,
        url_pattern: str,
        method: str | None = None,
        timeout_ms: int = 15000,
        *,
        ssr_state_key: str | None = None,
        ssr_state_path: str | None = None,
    ) -> dict:
        """Return the body of an intercepted XHR matching `url_pattern`.

        Interception is armed when the page is acquired, not here: a page's
        own load-time requests fire before any step runs, so a listener
        attached at wait time would always miss them (USER_SOP G24). This
        drains the buffer that the listener fills; an empty return means the
        response never came and the caller should fall back to the DOM.

        SSR fallback (2026-07-31): if ``ssr_state_key`` is provided and XHR
        times out, attempts to read the page's SSR hydration global variable
        (e.g. window.__INITIAL_STATE__) via page.evaluate before giving up.
        """
        self._arm_interception(page)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:  # deadline-bounded poll
            captured = self._take_captured(url_pattern, method)
            if captured is not None:
                return captured
            await asyncio.sleep(_XHR_POLL_INTERVAL)
        captured = self._take_captured(url_pattern, method)
        if captured is not None:
            return captured
        # SSR hydration fallback: read embedded data from JS global variable
        if ssr_state_key:
            ssr_data = await self._try_ssr_state(page, ssr_state_key, ssr_state_path)
            if ssr_data:
                logger.info(
                    "XHR timeout for '%s'; SSR state '%s' provided data",
                    url_pattern, ssr_state_key,
                )
                return ssr_data
        logger.warning("XHR timeout for pattern '%s', falling back to DOM", url_pattern)
        return {}

    async def _try_ssr_state(
        self,
        page: Any,
        state_key: str,
        state_path: str | None = None,
    ) -> dict | None:
        """Try to read SSR hydration data from a page global variable.

        Args:
            state_key: JS expression for the root state (e.g. "window.__INITIAL_STATE__").
            state_path: Optional dot-path into the state object (e.g. "note.noteList").

        Returns a dict wrapping the data (mimics XHR response structure) or None
        on failure.  Only reads — never modifies page state (safety constraint).
        """
        try:
            # Build a safe JS expression that reads the state and traverses the path
            js_expr = state_key
            if state_path:
                for segment in state_path.split("."):
                    js_expr += f"?.{segment}"
            # Wrap in JSON.stringify so we get serializable text back
            script = f"JSON.stringify(({js_expr}) || null)"
            raw = await page.evaluate(script)
            if not raw or raw == "null":
                return None
            data = json.loads(raw)
            if data:
                # Wrap in a structure that mimics an XHR response body so that
                # downstream extract_api can process it with the same JSONPath map.
                if isinstance(data, list):
                    return {"data": {"items": data}, "success": True, "_source": "ssr"}
                if isinstance(data, dict):
                    return {**data, "_source": "ssr"}
            return None
        except Exception as exc:
            logger.debug("SSR state read failed (%s): %s", state_key, exc)
            return None

    def _arm_interception(self, page: Any) -> None:
        """Start recording responses of `page` (idempotent per page)."""
        if self._armed_page is page:
            return
        try:
            page.on("response", self._capture_response)
        except Exception as exc:
            logger.warning("Cannot listen for responses: %s", exc)
            return
        self._armed_page = page
        self._xhr_buffer = []

    def _capture_response(self, response: Any) -> None:
        """Response listener: read the body off the event loop, then buffer it."""
        try:
            asyncio.get_running_loop().create_task(self._buffer_response(response))
        except RuntimeError:  # no running loop (listener fired after teardown)
            pass

    async def _buffer_response(self, response: Any) -> None:
        try:
            url = response.url
            body = await response.text()
        except Exception as exc:
            logger.debug("Skipping unreadable response: %s", exc)
            return
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            data = {"raw": body}
        request = getattr(response, "request", None)
        self._xhr_buffer.append({
            "url": url,
            "method": (getattr(request, "method", "") or "").upper(),
            "data": data,
        })
        if len(self._xhr_buffer) > _XHR_BUFFER_LIMIT:
            del self._xhr_buffer[:-_XHR_BUFFER_LIMIT]

    def _take_captured(self, url_pattern: str, method: str | None) -> dict | None:
        """Pop the oldest buffered response matching pattern (+ method)."""
        for index, entry in enumerate(self._xhr_buffer):
            if url_pattern and url_pattern not in entry["url"]:
                continue
            if method and entry["method"] and entry["method"] != method.upper():
                continue
            self._xhr_buffer.pop(index)
            return entry["data"]
        return None

    def _locator_to_css(self, locator) -> str | None:
        """Convert a Locator to a CSS selector string."""
        if locator is None:
            return None
        if hasattr(locator, "css") and locator.css:
            return locator.css
        if hasattr(locator, "text") and locator.text:
            return f'text="{locator.text}"'
        return None

    # --- BasePlatformScraper interface ---

    async def search(self, keyword: str, sort: str = "general") -> list[ItemRef]:
        """Run search flow and return list of ItemRef."""
        resolved_sort = self.spec.sort_values.get(sort, sort)
        items = await self.run_flow("search", keyword=keyword, sort=resolved_sort)
        result = []
        for item in items:
            if isinstance(item, ItemRef):
                result.append(item)
            elif isinstance(item, dict):
                try:
                    result.append(ItemRef(platform=self.spec.platform, **item))
                except Exception:
                    pass
        return result

    async def fetch_item(self, ref: ItemRef) -> ScrapedPost:
        """Run the detail flow and return one merged ScrapedPost.

        A recorded detail flow usually extracts several groups from the same
        response (Post.body and Post.interactions); they describe the *same*
        post, so they are merged instead of keeping only the first one
        (USER_SOP G29: interaction counts used to be discarded).
        """
        items = await self.run_flow("detail", item_id=ref.item_id)
        merged: dict[str, Any] = {"platform": self.spec.platform, "platform_id": ref.item_id}
        for item in items:
            fields = item.model_dump() if isinstance(item, ScrapedPost) else item
            if not isinstance(fields, dict):
                continue
            merged.update({
                key: value for key, value in fields.items()
                if value not in (None, [], "") and key in ScrapedPost.model_fields
            })
        return ScrapedPost(**merged)

    async def fetch_comments(self, ref: ItemRef) -> list[ScrapedComment]:
        """Run comments flow and return list of ScrapedComment."""
        items = await self.run_flow("comments", item_id=ref.item_id)
        result = []
        for item in items:
            if isinstance(item, ScrapedComment):
                result.append(item)
            elif isinstance(item, dict):
                try:
                    result.append(ScrapedComment(**{k: v for k, v in item.items() if k in ScrapedComment.model_fields}))
                except Exception:
                    pass
        return result

    async def login(self) -> dict:
        """Run login flow."""
        login = self.spec.login
        return {
            "type": login.type,
            "login_url": login.login_url,
            "status": "pending",
        }
