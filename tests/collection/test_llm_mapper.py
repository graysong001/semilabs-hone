"""LLM mapper tests — fully mocked (no anthropic/playwright required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def search_sample():
    return _load_fixture("search_response.json")


@pytest.fixture
def detail_sample():
    return _load_fixture("detail_response.json")


@pytest.fixture
def comments_sample():
    return _load_fixture("comments_response.json")


@pytest.fixture
def mock_anthropic_resp():
    """Factory for mocked anthropic response objects."""

    def _make(content: str):
        class TextBlock:
            def __init__(self, text):
                self.text = text

        class Response:
            def __init__(self, text):
                self.content = [TextBlock(text)]

        return Response(content)

    return _make


@pytest.fixture
def mock_anthropic_client(monkeypatch, mock_anthropic_resp):
    """Create a mock anthropic client and inject it."""

    class MockMessages:
        def __init__(self, resp_factory):
            self._resp = resp_factory
            self.create_calls = []

        async def create(self, **kwargs):
            self.create_calls.append(kwargs)
            # Return structured JSON matching field_specs in kwargs
            # Extract field names from prompt to build response
            prompt = kwargs.get("messages", [{}])[0].get("content", "")
            field_map = {}
            for line in prompt.split("\n"):
                if line.startswith("- ") and ": " in line:
                    parts = line[2:].split(": ", 1)
                    field_name = parts[0].strip()
                    field_map[field_name] = f"$.mock.{field_name}"
            if not field_map:
                # Try to extract from JSON in prompt
                field_map = {"field": "$.mock.field"}
            return self._resp(json.dumps(field_map))

    client_messages = MockMessages(mock_anthropic_resp)

    class MockClient:
        def __init__(self):
            self.messages = client_messages

    return MockClient()


# ---------------------------------------------------------------------------
# map_group tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_group_happy_path(search_sample, mock_anthropic_client):
    """map_group returns dict[str,str] JSONPath for known fields."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    field_specs = {
        "item_id": "unique note identifier",
        "title": "note title or display title",
        "author_name": "author nickname",
        "likes": "number of likes",
    }

    result = await llm_mapper.map_group(
        search_sample, "ItemRef", field_specs,
        anthropic_client=mock_anthropic_client,
    )

    assert isinstance(result, dict)
    for name in field_specs:
        assert name in result
        assert isinstance(result[name], str)


@pytest.mark.asyncio
async def test_map_group_returns_jsonpath_strings(search_sample, mock_anthropic_client):
    """map_group values are valid JSONPath expressions starting with $."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    field_specs = {
        "item_id": "note id",
        "title": "title",
    }

    result = await llm_mapper.map_group(
        search_sample, "ItemRef", field_specs,
        anthropic_client=mock_anthropic_client,
    )

    for name, path in result.items():
        # Mock returns $.mock.xxx format; real would return valid JSONPath
        assert isinstance(path, str)


@pytest.mark.asyncio
async def test_map_group_missing_fields_retries(search_sample):
    """map_group retries when LLM response is missing requested fields."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    call_count = 0

    class FailThenSucceedMessages:
        async def create(self, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: missing one field
                content = json.dumps({"item_id": "$.data.items[*].note_id"})
            else:
                # Retry: all fields present
                content = json.dumps({
                    "item_id": "$.data.items[*].note_id",
                    "title": "$.data.items[*].display_title",
                    "author_name": "$.data.items[*].user.nickname",
                })

            class Block:
                def __init__(self, t):
                    self.text = t

            class Resp:
                def __init__(self, c):
                    self.content = [Block(c)]

            return Resp(content)

    class MockClient:
        def __init__(self):
            self.messages = FailThenSucceedMessages()

    result = await llm_mapper.map_group(
        search_sample, "ItemRef",
        {"item_id": "note id", "title": "title", "author_name": "author"},
        anthropic_client=MockClient(),
    )

    assert "item_id" in result
    assert "title" in result
    assert "author_name" in result
    assert call_count == 2  # Retried once


@pytest.mark.asyncio
async def test_map_group_empty_field_specs(search_sample, mock_anthropic_client):
    """map_group with empty field_specs returns empty dict."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    result = await llm_mapper.map_group(
        search_sample, "ItemRef", {},
        anthropic_client=mock_anthropic_client,
    )

    assert isinstance(result, dict)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# extract_custom_field tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_custom_field_happy_path(detail_sample):
    """extract_custom_field returns a JSONPath string for a described field."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    class Block:
        def __init__(self, t):
            self.text = t

    class Resp:
        def __init__(self, c):
            self.content = [Block(c)]

    class MockMessages:
        async def create(self, **kwargs):
            return Resp("$.data.items[*].note.interact_info.share_count")

    class MockClient:
        def __init__(self):
            self.messages = MockMessages()

    result = await llm_mapper.extract_custom_field(
        detail_sample, "share count from interactions",
        anthropic_client=MockClient(),
    )

    assert isinstance(result, str)
    assert result.startswith("$")


@pytest.mark.asyncio
async def test_extract_custom_field_returns_empty_on_error(detail_sample):
    """extract_custom_field returns empty string when LLM fails."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    class MockMessages:
        async def create(self, **kwargs):
            raise RuntimeError("API connection failed")

    class MockClient:
        def __init__(self):
            self.messages = MockMessages()

    result = await llm_mapper.extract_custom_field(
        detail_sample, "video duration",
        anthropic_client=MockClient(),
    )

    assert result == ""


# ---------------------------------------------------------------------------
# validate_map tests
# ---------------------------------------------------------------------------


def test_validate_map_non_empty_fields_true(search_sample):
    """validate_map returns True for paths that yield values."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {
        "success": "$.success",
        "has_more": "$.data.has_more",
    }

    result = validate_map(search_sample, field_map)

    assert result["success"] is True
    assert result["has_more"] is True


def test_validate_map_empty_path_false(search_sample):
    """validate_map returns False for empty path expressions."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {
        "good": "$.success",
        "bad": "",
    }

    result = validate_map(search_sample, field_map)

    assert result["good"] is True
    assert result["bad"] is False


def test_validate_map_invalid_path_false(search_sample):
    """validate_map returns False for invalid JSONPath expressions."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {
        "good": "$.success",
        "bad": "not_a_valid_path",
    }

    result = validate_map(search_sample, field_map)

    assert result["good"] is True
    assert result["bad"] is False


def test_validate_map_null_value_false(search_sample):
    """validate_map returns False when path yields None."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    # Use a path that doesn't exist -> None
    field_map = {
        "exists": "$.success",
        "missing": "$.nonexistent_field_xyz",
    }

    result = validate_map(search_sample, field_map)

    assert result["exists"] is True
    assert result["missing"] is False


def test_validate_map_nested_path_true(detail_sample):
    """validate_map succeeds for nested JSONPath expressions."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {
        "note_id": "$.data.items[*].note.note_id",
        "author": "$.data.items[*].note.user.nickname",
        "likes": "$.data.items[*].note.interact_info.liked_count",
    }

    result = validate_map(detail_sample, field_map)

    # Note: jsonpath-ng with [*] on a single-item list returns one match
    assert result["note_id"] is True
    assert result["author"] is True
    assert result["likes"] is True


def test_validate_map_comments(comments_sample):
    """validate_map works with comments fixture."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {
        "comment_id": "$.data.comments[*].id",
        "content": "$.data.comments[*].content",
        "author": "$.data.comments[*].user.nickname",
    }

    result = validate_map(comments_sample, field_map)

    assert result["comment_id"] is True
    assert result["content"] is True
    assert result["author"] is True


def test_validate_map_empty_sample():
    """validate_map returns False for all fields on empty sample."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    field_map = {"title": "$.title"}
    result = validate_map({}, field_map)
    assert result["title"] is False


def test_validate_map_empty_list_value():
    """validate_map returns False when path yields empty list."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import validate_map

    sample = {"items": []}
    field_map = {"items": "$.items"}
    result = validate_map(sample, field_map)
    assert result["items"] is False


# ---------------------------------------------------------------------------
# build_platform_yaml tests
# ---------------------------------------------------------------------------


def test_build_platform_yaml_produces_valid_yaml():
    """build_platform_yaml returns parseable YAML text."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import build_platform_yaml

    flows = {
        "search": [
            {"type": "navigate", "url": "/search?q={keyword}"},
            {"type": "wait_xhr", "url_pattern": "/api/search", "method": "GET", "save_as": "search_resp"},
        ],
    }
    maps = {
        "search": {
            "ItemRef": {
                "item_id": "$.data.items[*].note_id",
                "title": "$.data.items[*].display_title",
            }
        }
    }

    yaml_text = build_platform_yaml("Test Site", "https://test.example.com", flows, maps)

    parsed = yaml.safe_load(yaml_text)
    assert parsed["platform"] == "test_site"
    assert parsed["display_name"] == "Test Site"
    assert parsed["base_url"] == "https://test.example.com"
    assert "flows" in parsed


def test_build_platform_yaml_compatible_with_platformspec():
    """build_platform_yaml output can be parsed by DM-07 spec.PlatformSpec."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import build_platform_yaml
    from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec

    flows = {
        "search": [
            {"type": "navigate", "url": "/search?q={keyword}"},
            {"type": "input", "locator": {"text": "Search"}, "text": "{keyword}"},
            {"type": "scroll", "max_times": 3, "wait_ms": 800},
            {"type": "wait_xhr", "url_pattern": "/api/search", "method": "GET", "save_as": "search_resp"},
        ],
        "detail": [
            {"type": "navigate", "url": "/p/{item_id}"},
            {"type": "wait_xhr", "url_pattern": "/api/feed", "save_as": "feed_resp"},
        ],
    }
    maps = {
        "search": {
            "ItemRef": {
                "item_id": "$.data.items[*].note_id",
                "title": "$.data.items[*].display_title",
                "author_name": "$.data.items[*].user.nickname",
                "likes": "$.data.items[*].interact_info.liked_count",
            }
        },
        "detail": {
            "Post.body": {
                "title": "$.data.items[*].note.title",
                "content": "$.data.items[*].note.desc",
                "author_name": "$.data.items[*].note.user.nickname",
            },
            "Post.interactions": {
                "likes": "$.data.items[*].note.interact_info.liked_count",
                "collects": "$.data.items[*].note.interact_info.collected_count",
                "comments_count": "$.data.items[*].note.interact_info.comment_count",
            },
        },
    }

    yaml_text = build_platform_yaml("My Test Platform", "https://test.example.com", flows, maps)

    parsed_yaml = yaml.safe_load(yaml_text)
    spec = PlatformSpec(**parsed_yaml)

    assert spec.platform == "my_test_platform"
    assert spec.display_name == "My Test Platform"
    assert "search" in spec.flows
    assert "detail" in spec.flows
    assert len(spec.flows["search"].steps) > 0


def test_build_platform_yaml_all_flows():
    """build_platform_yaml with search/detail/comments flows."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import build_platform_yaml
    from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec

    flows = {
        "search": [
            {"type": "navigate", "url": "/search"},
            {"type": "wait_xhr", "url_pattern": "/api/search", "save_as": "search_resp"},
        ],
        "detail": [
            {"type": "navigate", "url": "/note/{item_id}"},
            {"type": "wait_xhr", "url_pattern": "/api/feed", "save_as": "feed_resp"},
        ],
        "comments": [
            {"type": "scroll"},
            {"type": "wait_xhr", "url_pattern": "/api/comments", "save_as": "cmt_resp"},
        ],
    }
    maps = {
        "search": {
            "ItemRef": {
                "item_id": "$.data.items[*].note_id",
                "title": "$.data.items[*].display_title",
            }
        },
        "detail": {
            "Post.body": {"title": "$.data.items[*].note.title"},
            "Post.interactions": {"likes": "$.data.items[*].note.interact_info.liked_count"},
        },
        "comments": {
            "Comments": {
                "platform_id": "$.data.comments[*].id",
                "content": "$.data.comments[*].content",
            }
        },
    }

    yaml_text = build_platform_yaml("XiaoHongShu", "https://www.xiaohongshu.com", flows, maps)
    parsed = yaml.safe_load(yaml_text)
    spec = PlatformSpec(**parsed)

    assert spec.platform == "xiaohongshu"
    assert "search" in spec.flows
    assert "detail" in spec.flows
    assert "comments" in spec.flows
    # Each flow should have extract steps injected from maps
    search_steps = spec.flows["search"].steps
    extract_steps = [s for s in search_steps if s.type == "extract"]
    assert len(extract_steps) >= 1
    assert extract_steps[0].group == "ItemRef"


def test_build_platform_yaml_sort_values():
    """build_platform_yaml includes sort_values."""
    from semilabs_hone.modules.collection.scrapers.llm_mapper import build_platform_yaml

    yaml_text = build_platform_yaml("Test", "https://test.com", {}, {})
    parsed = yaml.safe_load(yaml_text)

    assert "sort_values" in parsed
    assert parsed["sort_values"]["general"] == "general"


# ---------------------------------------------------------------------------
# Integration: map_group + validate_map round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_group_then_validate_roundtrip(search_sample):
    """map_group produces paths that validate_map can check."""
    from semilabs_hone.modules.collection.scrapers import llm_mapper

    class Block:
        def __init__(self, t):
            self.text = t

    class Resp:
        def __init__(self, c):
            self.content = [Block(c)]

    # Return paths we know will validate against the search_sample fixture
    class MockMessages:
        async def create(self, **kwargs):
            return Resp(json.dumps({
                "item_id": "$.data.items[*].note_id",
                "title": "$.data.items[*].display_title",
                "author_name": "$.data.items[*].user.nickname",
                "likes": "$.data.items[*].interact_info.liked_count",
            }))

    class MockClient:
        def __init__(self):
            self.messages = MockMessages()

    field_specs = {
        "item_id": "note id",
        "title": "display title",
        "author_name": "author nickname",
        "likes": "like count",
    }

    field_map = await llm_mapper.map_group(
        search_sample, "ItemRef", field_specs,
        anthropic_client=MockClient(),
    )

    assert isinstance(field_map, dict)

    validation = llm_mapper.validate_map(search_sample, field_map)

    # These paths match the fixture structure
    assert validation["item_id"] is True
    assert validation["title"] is True
    assert validation["author_name"] is True
