"""GenericEngine — platform-agnostic step-chain replay + JSONPath extraction + light LLM fallback."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from semilabs_hone.core.models.schemas import ItemRef, ScrapedPost, ScrapedComment
from semilabs_hone.modules.collection.scrapers.base import (
    GROUP_COMMENTS,
    GROUP_ITEM_REF,
    GROUP_POST_BODY,
    GROUP_POST_INTERACTIONS,
    BasePlatformScraper,
)
from semilabs_hone.modules.collection.scrapers.field_extract import extract_api, render_template
from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec

logger = logging.getLogger(__name__)


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
        # Optional risk-probe callback set by the handler: async (page) -> hit|None.
        # When set, run_flow fires it after navigate/scroll/scroll_collect/click
        # and raises RiskProbeHit on a hit (PRD §4.4.1).
        self.on_risk: Callable[[Any], Awaitable[Any]] | None = None

    async def _ensure_page(self) -> Any:
        """Get or create a page from context. Mockable."""
        if self.page is None and self.ctx is not None:
            try:
                pages = self.ctx.pages if hasattr(self.ctx, "pages") else []
                self.page = pages[0] if pages else await self.ctx.new_page()
            except Exception:
                pass
        if self.page is None:
            raise RuntimeError("No page available; provide a context or set engine.page")
        return self.page

    async def run_flow(self, flow_name: str, **vars: Any) -> list:
        """Replay a flow's step chain and return extracted items."""
        flow = self.spec.flows.get(flow_name)
        if not flow:
            logger.warning("Flow '%s' not found in spec '%s'", flow_name, self.spec.platform)
            return []

        saved: dict[str, Any] = {}
        out: list = []
        page = await self._ensure_page()

        for step in flow.steps:
            match step.type:
                case "navigate":
                    url = render_template(step.url or "", **vars)
                    await page.goto(url)
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
                    )
                    if step.save_as:
                        saved[step.save_as] = resp_data

                case "extract":
                    resp = saved.get(step.from_ or "")
                    if resp is not None and step.group and step.map:
                        items = extract_api(resp, step.group, step.map)
                        validated = await self._validate_group(items, step.group)
                        out.extend(validated)

                case "wait_selector":
                    if step.selector:
                        try:
                            await page.wait_for_selector(step.selector, timeout=5000)
                        except Exception:
                            logger.warning("Selector not found: %s", step.selector)

        return out

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

        Re-extracts ``from_``/``group``/``map`` from the saved XHR snapshot after
        each wheel scroll, dedups against already-collected ids, appends fresh
        validated items to ``out``. Stops at ``max_scrolls`` (default 20) or
        ``empty_break`` (default 5) consecutive scrolls with no new items —
        never deadlocks on an infinite loader.
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
            fresh = self._extract_dedup(resp, group, fmap, collected)
            if fresh:
                out.extend(await self._validate_group(fresh, group))
                consecutive_empty = 0
            else:
                consecutive_empty += 1

        logger.debug(
            "scroll_collect: scrolls=%d items=%d consecutive_empty=%d",
            scrolls, len(out), consecutive_empty,
        )

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

        Injects `platform` from spec into ItemRef items.
        On failure: try LLM fallback for individual items.
        """
        model = _GROUP_MODEL_MAP.get(group)
        if not model:
            return items  # No model to validate against

        validated = []
        for item in items:
            # Inject platform from spec for ItemRef
            if group == GROUP_ITEM_REF:
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

    async def _llm_fallback(self, item: dict, group: str):
        """Light LLM fallback — lazy import anthropic."""
        if self._llm_fail_count >= self._llm_fail_threshold:
            logger.warning(
                "LLM fallback threshold reached (%d); suggest re-recording flow",
                self._llm_fail_count,
            )
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
                model="claude-haiku-4-5-20250414",
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
    ) -> dict:
        """Wait for an XHR response matching url_pattern.

        Uses page.on('response') + Future + wait_for timeout,
        then falls back to DOM extraction.
        """
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def _capture(response: Any) -> None:
            try:
                resp_url = response.url if hasattr(response, "url") else ""
                resp_method = response.request.method if hasattr(response, "request") and hasattr(response.request, "method") else ""
                logger.debug("_wait_xhr._capture: url=%s pattern_match=%s method=%s",
                             resp_url[:60], url_pattern in resp_url, resp_method)
                if url_pattern in resp_url:
                    if method is None or resp_method.upper() == method.upper():
                        if not fut.done():
                            logger.debug("_wait_xhr._capture: setting future result")
                            fut.set_result(response)
            except Exception as e:
                logger.warning("_wait_xhr._capture exception: %s", e)

        page.on("response", _capture)
        logger.debug("_wait_xhr: waiting for pattern '%s' (timeout=%dms)", url_pattern, timeout_ms)
        try:
            response = await asyncio.wait_for(
                asyncio.shield(fut),
                timeout=timeout_ms / 1000.0,
            )
            try:
                return await response.json()
            except Exception:
                text = await response.text()
                try:
                    return json.loads(text)
                except Exception:
                    return {"raw": text}
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning(
                "XHR timeout for pattern '%s', falling back to DOM",
                url_pattern,
            )
            return {}
        finally:
            try:
                page.remove_listener("response", _capture)
            except Exception:
                pass

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
        """Run detail flow and return a ScrapedPost."""
        items = await self.run_flow("detail", item_id=ref.item_id)
        if items:
            item = items[0]
            if isinstance(item, ScrapedPost):
                return item
            if isinstance(item, dict):
                return ScrapedPost(**{k: v for k, v in item.items() if k in ScrapedPost.model_fields})
        return ScrapedPost(platform_id=ref.item_id)

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
