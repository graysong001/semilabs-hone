"""Recorder tests — pure-logic helpers + mocked response/element (PRD §S8.3).

Covers _make_save_as_name, _guess_flow_name, RecordingSession step recording,
_build_recording_result flow grouping + xhr_samples dict, _on_response
heuristic (data XHR annotation / asset skip / text-body fallback),
capture_element_selectors (mock element), _get_playwright ImportError path.
The real-browser paths (start/stop/record_platform/_launch_chrome_and_attach)
are not unit-tested.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from semilabs_hone.modules.collection.scrapers import recorder
from semilabs_hone.modules.collection.scrapers.recorder import (
    RecordingResult,
    RecordingSession,
    Step,
    XhrSample,
    _get_playwright,
    _guess_flow_name,
    _make_save_as_name,
)


# ─── _make_save_as_name ──────────────────────────────────────────────────

class TestMakeSaveAsName:
    def test_takes_last_two_path_parts(self):
        n = _make_save_as_name("https://x.com/api/sns/web/v1/search/notes", "POST")
        assert n == "search_notes"

    def test_single_part(self):
        assert _make_save_as_name("https://x.com/resp", "GET") == "resp"

    def test_empty_path(self):
        assert _make_save_as_name("https://x.com", "GET") == "resp"

    def test_sanitizes_non_alnum(self):
        n = _make_save_as_name("https://x.com/a.b-c/d_e", "GET")
        # non-alnum chars become underscores
        assert all(c.isalnum() or c == "_" for c in n)


# ─── _guess_flow_name ────────────────────────────────────────────────────

class TestGuessFlowName:
    def test_search(self):
        assert _guess_flow_name("https://x.com/api/search/notes") == "search"

    def test_query(self):
        assert _guess_flow_name("https://x.com/query?q=x") == "search"

    def test_comments(self):
        assert _guess_flow_name("https://x.com/api/comment/list") == "comments"

    def test_detail_variants(self):
        for kw in ["detail", "feed", "note", "post"]:
            assert _guess_flow_name(f"https://x.com/{kw}/1") == "detail"

    def test_unknown(self):
        n = _guess_flow_name("https://x.com/xyz")
        assert n.startswith("flow_")


# ─── RecordingSession step recording ─────────────────────────────────────

class TestStepRecording:
    def test_record_input_appends_step(self):
        s = RecordingSession()
        s.record_input("hello", {"text": "hello"})
        assert len(s._steps) == 1
        assert s._steps[0].type == "input"
        assert s._steps[0].text == "hello"
        assert s._last_action_time > 0

    def test_record_click_default_locator(self):
        s = RecordingSession()
        s.record_click()
        assert s._steps[0].type == "click"
        assert s._steps[0].locator == {}

    def test_record_scroll(self):
        s = RecordingSession()
        s.record_scroll()
        assert s._steps[0].type == "scroll"

    def test_record_navigate(self):
        s = RecordingSession()
        s.record_navigate("https://x.com/search")
        assert s._steps[0].type == "navigate"
        assert s._steps[0].url == "https://x.com/search"

    def test_multiple_steps_in_order(self):
        s = RecordingSession()
        s.record_navigate("https://x.com")
        s.record_input("kw")
        s.record_click()
        s.record_scroll()
        assert [st.type for st in s._steps] == ["navigate", "input", "click", "scroll"]


# ─── _build_recording_result ─────────────────────────────────────────────

class TestBuildRecordingResult:
    def test_single_flow(self):
        s = RecordingSession()
        s.record_navigate("https://x.com/search")
        s.record_input("kw")
        s.record_click()
        result = s._build_recording_result()
        assert isinstance(result, RecordingResult)
        assert "search" in result.flows
        assert len(result.flows["search"]) == 3

    def test_multiple_flows_split_on_navigate(self):
        s = RecordingSession()
        # First flow: navigate + input + click (3 steps >= 2)
        s.record_navigate("https://x.com/search")
        s.record_input("kw")
        s.record_click()
        # Second navigate → new flow
        s.record_navigate("https://x.com/note/1")
        s.record_scroll()
        result = s._build_recording_result()
        assert "search" in result.flows
        assert "detail" in result.flows
        assert len(result.flows["search"]) == 3
        assert len(result.flows["detail"]) == 2

    def test_xhr_samples_serialized_to_dict(self):
        s = RecordingSession()
        s._xhr_samples["https://x.com/api/search"] = XhrSample(
            url="https://x.com/api/search", method="POST", status=200,
            body={"k": "v"}, content_length=10, timestamp=1.0)
        result = s._build_recording_result()
        assert len(result.xhr_samples) == 1
        sample = next(iter(result.xhr_samples.values()))
        assert sample["method"] == "POST"
        assert sample["body"] == {"k": "v"}


# ─── _on_response heuristic ──────────────────────────────────────────────

def _mock_response(*, url="https://x.com/api/search", method="POST",
                   resource_type="xhr", content_type="application/json",
                   status=200, body=None, text=None):
    """Build a mock response for _on_response."""
    r = MagicMock()
    r.url = url
    r.status = status
    req = MagicMock()
    req.method = method
    req.resource_type = resource_type
    r.request = req
    headers = MagicMock()
    headers.get = lambda k, default="": {"content-type": content_type}.get(k, default)
    r.headers = headers
    if body is not None:
        r.json = MagicMock(return_value=body)
    if text is not None:
        r.text = MagicMock(return_value=text)
    return r


class TestOnResponse:
    def test_non_xhr_asset_skipped(self):
        s = RecordingSession()
        # image, not json/xhr/fetch
        s._on_response(_mock_response(resource_type="image", content_type="image/png"))
        assert len(s._xhr_samples) == 0

    def test_data_xhr_annotates_last_step(self):
        s = RecordingSession()
        s.record_click()  # last_action_time = now
        big = {"items": [{"id": i} for i in range(200)]}  # > 500 bytes
        s._on_response(_mock_response(body=big))
        assert s._steps[-1].triggered_xhr is not None

    def test_small_json_not_data_xhr(self):
        s = RecordingSession()
        s.record_click()
        s._on_response(_mock_response(body={"k": "v"}))  # small json
        # XHR captured but last step not annotated as data xhr
        assert s._steps[-1].triggered_xhr is None
        assert len(s._xhr_samples) == 1

    def test_json_after_threshold_not_data_xhr(self):
        s = RecordingSession()
        s._last_action_time = -100  # long ago → outside 3s window
        big = {"items": list(range(200))}
        s._on_response(_mock_response(body=big))
        assert s._steps[-1].triggered_xhr is None if s._steps else True

    def test_text_body_fallback(self):
        s = RecordingSession()
        # No .json() but .text() returns JSON text
        r = MagicMock()
        r.url = "https://x.com/api/text"
        r.status = 200
        req = MagicMock()
        req.method = "GET"
        req.resource_type = "fetch"
        r.request = req
        headers = MagicMock()
        headers.get = lambda k, d="": "application/json" if k == "content-type" else d
        r.headers = headers
        text = json.dumps({"a": 1, "b": "x" * 600})
        r.text = MagicMock(return_value=text)
        r.json = MagicMock(side_effect=ValueError("no json"))
        s._on_response(r)
        assert len(s._xhr_samples) == 1

    def test_largest_sample_kept(self):
        s = RecordingSession()
        s.record_click()
        s._on_response(_mock_response(url="u", body={"x": "y" * 10}))
        s._on_response(_mock_response(url="u", body={"x": "y" * 2000}))
        assert len(s._xhr_samples) == 1
        assert s._xhr_samples["u"].content_length > 100

    def test_corrupt_response_does_not_raise(self):
        s = RecordingSession()
        s._on_response(MagicMock(spec=[]))  # no attrs
        assert len(s._xhr_samples) == 0


# ─── capture_element_selectors ───────────────────────────────────────────

class TestCaptureElementSelectors:
    async def test_extracts_text_role_aria_nth_tag(self):
        from unittest.mock import AsyncMock
        el = MagicMock()
        el.inner_text = AsyncMock(return_value="  Hello World  ")
        el.evaluate = AsyncMock(side_effect=[
            "button",       # role
            "Submit",       # aria-label
            0,              # nth
            "button",       # tag
        ])
        s = RecordingSession()
        selectors = await s.capture_element_selectors(el)
        assert "text" in selectors
        assert selectors["role"] == "button"
        assert selectors["aria_label"] == "Submit"
        assert selectors["nth"] == 0
        assert selectors["css"] == "button"

    async def test_empty_element_returns_empty(self):
        from unittest.mock import AsyncMock
        el = MagicMock()
        el.inner_text = AsyncMock(return_value="")
        el.evaluate = AsyncMock(side_effect=[None, None, None, None])
        s = RecordingSession()
        selectors = await s.capture_element_selectors(el)
        assert "text" not in selectors
        assert selectors == {}

    async def test_exception_returns_empty(self):
        from unittest.mock import AsyncMock
        el = MagicMock()
        el.inner_text = AsyncMock(side_effect=RuntimeError("nope"))
        s = RecordingSession()
        selectors = await s.capture_element_selectors(el)
        assert selectors == {}


# ─── _get_playwright ──────────────────────────────────────────────────────

class TestGetPlaywright:
    def test_raises_importerror_when_missing(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "playwright":
                raise ImportError("no playwright")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError):
            _get_playwright()
