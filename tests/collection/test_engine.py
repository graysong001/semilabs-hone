"""Tests for GenericEngine: mock page run_flow, search, empty flow, etc."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from semilabs_hone.core.models.schemas import ItemRef
from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
from semilabs_hone.modules.collection.scrapers.spec import (
    Flow,
    LoginSpec,
    PlatformSpec,
    Step,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


class MockResponse:
    """Standalone mock response class."""

    def __init__(self, url: str, data: dict):
        self.url = url
        self._data = data
        self.request = type("MockRequest", (), {"method": "POST"})()

    async def json(self) -> dict:
        return self._data

    async def text(self) -> str:
        return json.dumps(self._data)


def _make_mock_page(search_response: dict | None = None, url_pattern: str = "/api/search"):
    """Create a mock page object for testing.

    Fires the XHR response when a 'response' listener is registered,
    using loop.call_soon so wait_for has already started.
    """
    resp = search_response or _load_fixture("search_response.json")

    class MockPage:
        def __init__(self):
            self._listeners: dict[str, list] = {}
            self._goto_called = False

        async def goto(self, url: str):
            self._goto_called = True

        def on(self, event: str, callback):
            self._listeners.setdefault(event, []).append(callback)
            # Fire the response via call_soon so wait_for starts before callback fires
            if event == "response" and self._goto_called:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    callback,
                    MockResponse(f"https://example.com{url_pattern}", resp),
                )

        def remove_listener(self, event: str, callback):
            if event in self._listeners:
                try:
                    self._listeners[event].remove(callback)
                except ValueError:
                    pass

        async def evaluate(self, js: str) -> str:
            return ""

        async def fill(self, selector: str, text: str):
            pass

        async def click(self, selector: str):
            pass

        async def wait_for_selector(self, selector: str, timeout: int = 5000):
            pass

    return MockPage()


def _make_test_spec(url_pattern: str = "/api/search") -> PlatformSpec:
    """Build a test PlatformSpec."""
    return PlatformSpec(
        platform="test_platform",
        display_name="Test Platform",
        base_url="https://test.example.com",
        login=LoginSpec(type="qrcode", login_url="/login", timeout=120),
        flows={
            "search": Flow(
                steps=[
                    Step(
                        type="navigate",
                        url="/search?q={keyword}&sort={sort}",
                    ),
                    Step(
                        type="wait_xhr",
                        url_pattern=url_pattern,
                        method="POST",
                        save_as="search_resp",
                        timeout_ms=15000,
                    ),
                    Step(
                        type="extract",
                        from_="search_resp",
                        group="ItemRef",
                        map={
                            "item_id": "$.note_id",
                            "title": "$.display_title",
                            "author_name": "$.user.nickname",
                            "likes": "$.interact_info.liked_count",
                        },
                    ),
                ]
            ),
        },
        sort_values={"general": "general", "time_descending": "latest"},
    )


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

class TestGenericEngineRunFlow:
    """Test GenericEngine.run_flow with mock page."""

    @pytest.mark.asyncio
    async def test_run_flow_search_returns_items(self):
        """run_flow('search') should return non-empty list of ItemRef dicts."""
        spec = _make_test_spec()
        engine = GenericEngine(spec=spec)
        page = _make_mock_page()
        engine.page = page

        results = await engine.run_flow("search", keyword="coffee", sort="general")

        assert isinstance(results, list)
        assert len(results) == 2
        assert results[0].item_id == "64abc123def456"
        assert results[0].title == "手冲咖啡入门指南"
        assert results[0].author_name == "咖啡爱好者"

    @pytest.mark.asyncio
    async def test_run_flow_unknown_returns_empty(self):
        """run_flow with unknown flow name returns empty list."""
        spec = _make_test_spec()
        engine = GenericEngine(spec=spec)
        page = _make_mock_page()
        engine.page = page

        results = await engine.run_flow("nonexistent_flow")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_method_returns_item_refs(self):
        """engine.search() should return list of ItemRef objects."""
        spec = _make_test_spec()
        engine = GenericEngine(spec=spec)
        page = _make_mock_page()
        engine.page = page

        items = await engine.search("coffee", "general")

        assert isinstance(items, list)
        assert len(items) == 2
        assert all(isinstance(item, ItemRef) for item in items)
        assert items[0].item_id == "64abc123def456"
        assert items[0].platform == "test_platform"


class TestGenericEngineLogin:
    """Test login flow."""

    @pytest.mark.asyncio
    async def test_login_returns_pending(self):
        spec = _make_test_spec()
        engine = GenericEngine(spec=spec)

        result = await engine.login()

        assert result["type"] == "qrcode"
        assert result["status"] == "pending"
        assert result["login_url"] == "/login"


class TestGenericEngineNoPage:
    """Test engine behavior when no page is available."""

    @pytest.mark.asyncio
    async def test_no_page_raises_runtime_error(self):
        spec = _make_test_spec()
        engine = GenericEngine(spec=spec)

        with pytest.raises(RuntimeError, match="No page available"):
            await engine.run_flow("search", keyword="test", sort="general")


class TestGenericEngineWithXhsYaml:
    """Test engine with the actual XHS platform.yaml."""

    @pytest.mark.asyncio
    async def test_xhs_search_flow(self):
        """Engine with XHS yaml + mock page + fixture should extract items."""
        yaml_path = (
            Path(__file__).parent.parent.parent
            / "semilabs_hone" / "modules" / "collection" / "scrapers"
            / "platforms" / "xiaohongshu" / "platform.yaml"
        )
        if not yaml_path.exists():
            pytest.skip("XHS platform.yaml not found")

        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
        spec = PlatformSpec(**data)
        engine = GenericEngine(spec=spec)

        search_data = _load_fixture("search_response.json")
        page = _make_mock_page(
            search_response=search_data,
            url_pattern="/api/sns/web/v1/search/notes",
        )
        engine.page = page

        items = await engine.run_flow("search", keyword="咖啡", sort="general")

        assert isinstance(items, list)
        assert len(items) == 2
        assert items[0].item_id == "64abc123def456"
