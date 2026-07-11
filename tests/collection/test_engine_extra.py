"""Engine extra coverage — step branches, human fallbacks, validate/llm, wait_xhr.

Covers the engine.py paths not hit by test_engine's search/scroll_collect:
run_flow input/click/scroll/go_back/wait_selector/extract branches,
_human_input/_human_click/_random_scroll ImportError fallbacks, _validate_group
+ _llm_fallback (mocked anthropic), _locator_to_css, fetch_item/fetch_comments
dict-conversion paths, _wait_xhr timeout + json + text fallback.
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from semilabs_hone.modules.collection.scrapers.engine import GenericEngine, RiskProbeHit
from semilabs_hone.modules.collection.scrapers.spec import Flow, LoginSpec, PlatformSpec, Step


def _spec_with_flow(flow_name: str, steps: list[Step], platform: str = "tp") -> PlatformSpec:
    return PlatformSpec(
        platform=platform, display_name="TP", base_url="https://x.example.com",
        login=LoginSpec(type="qrcode", login_url="/login"),
        flows={flow_name: Flow(steps=steps)},
    )


class _Page:
    """Minimal async page mock recording calls."""

    def __init__(self):
        self.goto = AsyncMock()
        self.fill = AsyncMock()
        self.click = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.go_back = AsyncMock()
        self.keyboard = MagicMock()
        self.keyboard.type = AsyncMock()
        self.mouse = MagicMock()
        self.mouse.wheel = AsyncMock()
        self.calls: dict[str, int] = {}

    def on(self, event, cb):
        pass

    def remove_listener(self, event, cb):
        pass


# ─── run_flow step branches ──────────────────────────────────────────────

class TestRunFlowBranches:
    async def test_input_step_uses_human_type(self, monkeypatch):
        from semilabs_hone.modules.collection.anti_detect import human_behavior
        monkeypatch.setattr(human_behavior, "human_type", AsyncMock())

        page = _Page()
        spec = _spec_with_flow("f", [
            Step(type="input", text="hello", locator={"text": "box"}),
        ])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")
        human_behavior.human_type.assert_called_once()

    async def test_click_step_uses_human_click(self, monkeypatch):
        from semilabs_hone.modules.collection.anti_detect import human_behavior
        monkeypatch.setattr(human_behavior, "human_click", AsyncMock())

        page = _Page()
        spec = _spec_with_flow("f", [Step(type="click", locator={"text": "go"})])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")
        human_behavior.human_click.assert_called_once()

    async def test_scroll_step_uses_random_scroll(self, monkeypatch):
        from semilabs_hone.modules.collection.anti_detect import human_behavior
        monkeypatch.setattr(human_behavior, "random_scroll", AsyncMock())

        page = _Page()
        spec = _spec_with_flow("f", [Step(type="scroll", max_times=3, wait_ms=10)])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")
        human_behavior.random_scroll.assert_called_once()

    async def test_go_back_step(self):
        page = _Page()
        spec = _spec_with_flow("f", [Step(type="navigate", url="/x"), Step(type="go_back")])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")
        page.go_back.assert_called_once()

    async def test_go_back_failure_does_not_raise(self):
        page = _Page()
        page.go_back = AsyncMock(side_effect=RuntimeError("no history"))
        spec = _spec_with_flow("f", [Step(type="go_back")])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")  # no raise

    async def test_wait_selector_found(self):
        page = _Page()
        spec = _spec_with_flow("f", [Step(type="wait_selector", selector=".x")])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")
        page.wait_for_selector.assert_called_once()

    async def test_wait_selector_missing_no_raise(self):
        page = _Page()
        page.wait_for_selector = AsyncMock(side_effect=RuntimeError("not found"))
        spec = _spec_with_flow("f", [Step(type="wait_selector", selector=".x")])
        eng = GenericEngine(spec=spec); eng.page = page
        await eng.run_flow("f")  # no raise

    async def test_extract_step_validates_and_extends(self):
        page = _Page()
        spec = _spec_with_flow("f", [
            Step(type="wait_xhr", url_pattern="/api", method="POST",
                 save_as="r", timeout_ms=10),
            Step(type="extract", from_="r", group="ItemRef",
                 map={"item_id": "$.note_id"}),
        ])
        eng = GenericEngine(spec=spec); eng.page = page
        # _wait_xhr returns {} (timeout) → extract sees no resp → out stays empty.
        items = await eng.run_flow("f")
        assert items == []


# ─── human fallbacks (ImportError path) ──────────────────────────────────

class TestHumanFallbacks:
    async def test_human_input_falls_back_to_fill(self, monkeypatch):
        # Force ImportError on human_behavior import inside _human_input.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if "human_behavior" in name:
                raise ImportError("no module")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        page = _Page()
        eng = GenericEngine(spec=_spec_with_flow("f", [])); eng.page = page
        loc = MagicMock(); loc.css = ".box"; loc.text = None
        await eng._human_input(page, loc, "hello")
        page.fill.assert_called_once()

    async def test_human_click_falls_back_to_click(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if "human_behavior" in name:
                raise ImportError("no module")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)

        page = _Page()
        eng = GenericEngine(spec=_spec_with_flow("f", [])); eng.page = page
        loc = MagicMock(); loc.css = ".go"; loc.text = None
        await eng._human_click(page, loc)
        page.click.assert_called_once()

    async def test_random_scroll_falls_back_to_wheel(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if "human_behavior" in name:
                raise ImportError("no module")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        page = _Page()
        eng = GenericEngine(spec=_spec_with_flow("f", [])); eng.page = page
        await eng._random_scroll(page, 2, 10)
        assert page.mouse.wheel.call_count >= 2


# ─── _locator_to_css ─────────────────────────────────────────────────────

class TestLocatorToCss:
    def test_none_returns_none(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        assert eng._locator_to_css(None) is None

    def test_css_attr(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        loc = MagicMock(); loc.css = ".btn"
        assert eng._locator_to_css(loc) == ".btn"

    def test_text_attr(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        loc = MagicMock(); loc.css = None; loc.text = "Submit"
        assert eng._locator_to_css(loc) == 'text="Submit"'

    def test_empty_returns_none(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        loc = MagicMock(); loc.css = None; loc.text = None
        assert eng._locator_to_css(loc) is None


# ─── _validate_group + _llm_fallback ──────────────────────────────────────

class TestValidateGroup:
    async def test_unknown_group_returns_items_as_is(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        items = [{"x": 1}]
        out = await eng._validate_group(items, "UnknownGroup")
        assert out == items

    async def test_llm_fallback_importerror_returns_none(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        # anthropic not importable → None
        out = await eng._llm_fallback({"item_id": "x"}, "ItemRef")
        assert out is None

    async def test_llm_fallback_threshold_returns_none(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        eng._llm_fail_count = eng._llm_fail_threshold
        out = await eng._llm_fallback({"item_id": "x"}, "ItemRef")
        assert out is None

    async def test_llm_fallback_success_parses_response(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        fake_msg = MagicMock()
        fake_msg.content = [MagicMock(text='{"item_id": "llm1", "platform": "tp"}')]
        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=fake_msg)

        fake_mod = types_module("anthic", AsyncAnthropic=lambda: fake_client)
        monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

        out = await eng._llm_fallback({"garbage": True}, "ItemRef")
        assert out is not None
        assert getattr(out, "item_id", None) == "llm1"

    async def test_llm_fallback_create_exception_returns_none(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=RuntimeError("api down"))
        fake_mod = types_module("anthic", AsyncAnthropic=lambda: fake_client)
        monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

        out = await eng._llm_fallback({"x": 1}, "ItemRef")
        assert out is None


def types_module(name, **attrs):
    mod = type(sys)(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ─── fetch_item / fetch_comments dict paths ──────────────────────────────

class TestFetchDictPaths:
    async def test_fetch_item_empty_flow_returns_default(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("detail", []))
        eng.page = _Page()
        monkeypatch.setattr(GenericEngine, "run_flow", AsyncMock(return_value=[]))
        from semilabs_hone.core.models.schemas import ItemRef
        post = await eng.fetch_item(ItemRef(platform="tp", item_id="x"))
        assert post.platform_id == "x"

    async def test_fetch_item_dict_converted_to_scrapedpost(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("detail", []))
        eng.page = _Page()
        monkeypatch.setattr(GenericEngine, "run_flow",
                            AsyncMock(return_value=[{"platform_id": "p1", "title": "t"}]))
        from semilabs_hone.core.models.schemas import ItemRef, ScrapedPost
        post = await eng.fetch_item(ItemRef(platform="tp", item_id="p1"))
        assert isinstance(post, ScrapedPost)
        assert post.title == "t"

    async def test_fetch_comments_dict_converted(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("comments", []))
        eng.page = _Page()
        monkeypatch.setattr(GenericEngine, "run_flow",
                            AsyncMock(return_value=[{"content": "hi", "author_name": "u"}]))
        from semilabs_hone.core.models.schemas import ItemRef, ScrapedComment
        cmts = await eng.fetch_comments(ItemRef(platform="tp", item_id="c1"))
        assert len(cmts) == 1
        assert isinstance(cmts[0], ScrapedComment)
        assert cmts[0].content == "hi"

    async def test_fetch_comments_bad_dict_skipped(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("comments", []))
        eng.page = _Page()
        monkeypatch.setattr(GenericEngine, "run_flow",
                            AsyncMock(return_value=[{"content": "ok"},
                                                    {"no_content": True}]))
        from semilabs_hone.core.models.schemas import ItemRef
        cmts = await eng.fetch_comments(ItemRef(platform="tp", item_id="c1"))
        assert len(cmts) == 1  # only the valid one


# ─── _wait_xhr ────────────────────────────────────────────────────────────

class TestWaitXhr:
    async def test_timeout_falls_back_to_empty_dict(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        page = _Page()  # on() never fires → timeout
        result = await eng._wait_xhr(page, "/never", "POST", timeout_ms=10)
        assert result == {}

    async def test_matching_json_response_captured(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))

        class _Resp:
            url = "https://x/api/data"
            request = type("R", (), {"method": "POST"})()

            async def json(self):
                return {"items": [1, 2]}

            async def text(self):
                return ""

        class _PageXhr:
            def on(self, event, cb):
                loop = asyncio.get_running_loop()
                loop.call_soon(cb, _Resp())

            def remove_listener(self, event, cb):
                pass

        result = await eng._wait_xhr(_PageXhr(), "/api/data", "POST", timeout_ms=1000)
        assert result == {"items": [1, 2]}

    async def test_text_body_fallback_when_json_fails(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))

        class _Resp:
            url = "https://x/api/text"
            request = type("R", (), {"method": "POST"})()

            async def json(self):
                raise ValueError("not json")

            async def text(self):
                return json.dumps({"k": "v"})

        class _PageXhr:
            def on(self, event, cb):
                loop = asyncio.get_running_loop()
                loop.call_soon(cb, _Resp())

            def remove_listener(self, event, cb):
                pass

        result = await eng._wait_xhr(_PageXhr(), "/api/text", "POST", timeout_ms=1000)
        assert result == {"k": "v"}


# ─── _probe / on_risk ─────────────────────────────────────────────────────

class TestProbe:
    async def test_no_on_risk_noop(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        await eng._probe(_Page())  # no raise

    async def test_on_risk_hit_raises(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        eng.on_risk = AsyncMock(return_value=object())
        with pytest.raises(RiskProbeHit):
            await eng._probe(_Page())

    async def test_on_risk_no_hit_noop(self):
        eng = GenericEngine(spec=_spec_with_flow("f", []))
        eng.on_risk = AsyncMock(return_value=None)
        await eng._probe(_Page())  # no raise


# ─── search dict-conversion path ──────────────────────────────────────────

class TestSearchDictPath:
    async def test_search_converts_dict_items(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("search", []))
        monkeypatch.setattr(GenericEngine, "run_flow",
                            AsyncMock(return_value=[{"item_id": "d1"}]))
        from semilabs_hone.core.models.schemas import ItemRef
        result = await eng.search("kw")
        assert len(result) == 1
        assert isinstance(result[0], ItemRef)
        assert result[0].item_id == "d1"

    async def test_search_skips_bad_dict(self, monkeypatch):
        eng = GenericEngine(spec=_spec_with_flow("search", []))
        monkeypatch.setattr(GenericEngine, "run_flow",
                            AsyncMock(return_value=[{"no_item_id": True}]))
        result = await eng.search("kw")
        assert result == []
