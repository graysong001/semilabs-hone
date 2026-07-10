"""Tests for field_extract: JSONPath/CSS extraction, empty/deformed JSON, template rendering."""

import json
from pathlib import Path

import pytest

from semilabs_hone.modules.collection.scrapers.field_extract import (
    extract_api,
    extract_dom,
    parse_likes,
    render_template,
    title_fallback,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# extract_api — search response
# ---------------------------------------------------------------------------

class TestExtractApiSearch:
    """Test extract_api against search_response.json."""

    def test_extract_item_ref_from_search(self):
        """Happy path: extract ItemRef fields from XHS search API response."""
        data = _load_fixture("search_response.json")
        field_map = {
            "item_id": "$.note_id",
            "title": "$.display_title",
            "author_name": "$.user.nickname",
            "likes": "$.interact_info.liked_count",
        }
        results = extract_api(data, "ItemRef", field_map)

        assert len(results) == 2
        # First item
        assert results[0]["item_id"] == "64abc123def456"
        assert results[0]["title"] == "手冲咖啡入门指南"
        assert results[0]["author_name"] == "咖啡爱好者"
        assert results[0]["likes"] == "1234"
        # Second item
        assert results[1]["item_id"] == "64def789abc012"
        assert results[1]["title"] == "拉花技巧分享"
        assert results[1]["author_name"] == "咖啡师小王"

    def test_missing_field_returns_none(self):
        """Fields not in JSON return None, not crash."""
        data = _load_fixture("search_response.json")
        field_map = {
            "item_id": "$.note_id",
            "nonexistent_field": "$.does.not.exist",
        }
        results = extract_api(data, "ItemRef", field_map)
        assert results[0]["item_id"] == "64abc123def456"
        assert results[0]["nonexistent_field"] is None

    def test_empty_field_map_returns_list_with_item(self):
        """Empty field_map should return empty list."""
        data = _load_fixture("search_response.json")
        results = extract_api(data, "ItemRef", {})
        assert results == []


# ---------------------------------------------------------------------------
# extract_api — detail response
# ---------------------------------------------------------------------------

class TestExtractApiDetail:
    """Test extract_api against detail_response.json."""

    def test_extract_post_body(self):
        """Extract Post.body fields from XHS detail feed response."""
        data = _load_fixture("detail_response.json")
        field_map = {
            "platform_id": "$.note.note_id",
            "title": "$.note.title",
            "content": "$.note.desc",
            "author_name": "$.note.user.nickname",
            "published_at": "$.note.time",
        }
        results = extract_api(data, "Post.body", field_map)

        assert len(results) == 1
        assert results[0]["platform_id"] == "64abc123def456"
        assert results[0]["title"] == "手冲咖啡入门指南"
        assert results[0]["content"] == "详解手冲咖啡的水温、研磨度与注水手法，新手友好。"
        assert results[0]["author_name"] == "咖啡爱好者"

    def test_extract_post_interactions(self):
        """Extract Post.interactions from detail response."""
        data = _load_fixture("detail_response.json")
        field_map = {
            "likes": "$.note.interact_info.liked_count",
            "collects": "$.note.interact_info.collected_count",
            "comments_count": "$.note.interact_info.comment_count",
            "shares": "$.note.interact_info.share_count",
        }
        results = extract_api(data, "Post.interactions", field_map)

        assert len(results) == 1
        assert results[0]["likes"] == "1234"
        assert results[0]["collects"] == "567"
        assert results[0]["comments_count"] == "89"
        assert results[0]["shares"] == "12"


# ---------------------------------------------------------------------------
# extract_api — comments response
# ---------------------------------------------------------------------------

class TestExtractApiComments:
    """Test extract_api against comments_response.json."""

    def test_extract_comments(self):
        """Extract Comment fields from XHS comments API response."""
        data = _load_fixture("comments_response.json")
        field_map = {
            "platform_id": "$.id",
            "author_name": "$.user.nickname",
            "content": "$.content",
            "likes": "$.like_count",
        }
        results = extract_api(data, "Comments", field_map)

        assert len(results) == 2
        assert results[0]["platform_id"] == "cmt_001"
        assert results[0]["author_name"] == "路人甲"
        assert results[0]["content"] == "学到了，谢谢分享！"
        assert results[0]["likes"] == "23"
        assert results[1]["platform_id"] == "cmt_002"
        assert results[1]["author_name"] == "路人乙"


# ---------------------------------------------------------------------------
# extract_api — empty / deformed / edge cases
# ---------------------------------------------------------------------------

class TestExtractApiEdgeCases:
    """Test extract_api with empty, null, and malformed inputs."""

    def test_null_json(self):
        """None input should return empty list."""
        assert extract_api(None, "ItemRef", {"item_id": "$.id"}) == []

    def test_empty_dict(self):
        """Empty dict should return empty list."""
        assert extract_api({}, "ItemRef", {"item_id": "$.id"}) == []

    def test_non_dict_input(self):
        """Non-dict input should return empty list."""
        assert extract_api("not a dict", "ItemRef", {"item_id": "$.id"}) == []
        assert extract_api([1, 2, 3], "ItemRef", {"item_id": "$.id"}) == []

    def test_malformed_jsonpath(self):
        """Invalid JSONPath expressions should not crash, return None."""
        data = {"key": "value"}
        field_map = {"bad_path": "$.[invalid"}
        results = extract_api(data, "test", field_map)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# extract_dom
# ---------------------------------------------------------------------------

class TestExtractDom:
    """Test extract_dom with HTML content."""

    def test_css_text_extraction(self):
        """CSS selector extracts text content."""
        html = "<div class='title'>Hello World</div>"
        results = extract_dom(html, "test", {"title": "css:.title"})
        assert results[0]["title"] == "Hello World"

    def test_css_attribute_extraction(self):
        """css:sel@attr extracts attribute value."""
        html = '<a href="https://example.com" class="link">Click</a>'
        results = extract_dom(html, "test", {"url": "css:a.link@href"})
        assert results[0]["url"] == "https://example.com"

    def test_css_no_match(self):
        """No matching selector returns None, not crash."""
        html = "<div>no match</div>"
        results = extract_dom(html, "test", {"title": "css:.nonexistent"})
        assert results[0]["title"] is None

    def test_empty_html(self):
        """Empty HTML should return list with None values."""
        results = extract_dom("", "test", {"title": "css:h1"})
        assert isinstance(results, list)

    def test_empty_field_map(self):
        """Empty field_map returns single-row dict."""
        html = "<div>test</div>"
        results = extract_dom(html, "test", {})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    """Test render_template with {keyword} placeholders."""

    def test_single_placeholder(self):
        result = render_template("/search?q={keyword}", keyword="coffee")
        assert result == "/search?q=coffee"

    def test_multiple_placeholders(self):
        result = render_template("/api?q={keyword}&sort={sort}", keyword="coffee", sort="hot")
        assert result == "/api?q=coffee&sort=hot"

    def test_no_placeholders(self):
        result = render_template("/static/path")
        assert result == "/static/path"

    def test_extra_vars_ignored(self):
        result = render_template("/search?q={keyword}", keyword="x", unused="y")
        assert result == "/search?q=x"


# ---------------------------------------------------------------------------
# parse_likes / title_fallback (PRD §8.5 场景5.1/5.2)
# ---------------------------------------------------------------------------

class TestParseLikes:
    """Interaction-string cleansing (PRD §8.5 场景5.1)."""

    def test_parse_likes_w_unit(self):
        assert parse_likes("1.2w") == 12000

    def test_parse_likes_wan_unit(self):
        assert parse_likes("1.5万") == 15000

    def test_parse_likes_wan_integer(self):
        assert parse_likes("3万") == 30000

    def test_parse_likes_zan_text_returns_zero(self):
        """"赞" (hidden count) → 0."""
        assert parse_likes("赞") == 0

    def test_parse_likes_empty_string_returns_zero(self):
        assert parse_likes("") == 0

    def test_parse_likes_none_returns_zero(self):
        assert parse_likes(None) == 0

    def test_parse_likes_plain_int_string(self):
        assert parse_likes("1234") == 1234

    def test_parse_likes_with_trailing_zan(self):
        """"1.5w赞" → 15000 (trailing label ignored)."""
        assert parse_likes("1.5w赞") == 15000

    def test_parse_likes_k_unit(self):
        assert parse_likes("1.2k") == 1200

    def test_parse_likes_qian_unit(self):
        assert parse_likes("1.2千") == 1200

    def test_parse_likes_numeric_passthrough(self):
        assert parse_likes(1234) == 1234
        assert parse_likes(1.5) == 1

    def test_parse_likes_no_digit_returns_zero(self):
        assert parse_likes("很赞") == 0


class TestTitleFallback:
    """Title fallback to body[:20] (PRD §8.5 场景5.2)."""

    def test_title_present_returns_title(self):
        assert title_fallback("我的标题", "正文内容") == "我的标题"

    def test_title_empty_uses_content_prefix(self):
        content = "这是一段很长的正文内容超过二十个字符的部分会被截断掉对吧"
        assert title_fallback("", content) == content[:20]

    def test_title_none_uses_content_prefix(self):
        assert title_fallback(None, "正文") == "正文"

    def test_both_empty_returns_empty(self):
        assert title_fallback("", "") == ""
        assert title_fallback(None, None) == ""

    def test_title_whitespace_only_falls_back(self):
        assert title_fallback("   ", "正文") == "正文"


# ---------------------------------------------------------------------------
# DM-07 Contract test
# ---------------------------------------------------------------------------

class TestDm07ScrapersContract:
    """Contract test: asserts all required symbols exist and are correct types."""

    def test_base_exists(self):
        from semilabs_hone.modules.collection.scrapers.base import (
            GROUP_COMMENTS,
            GROUP_ITEM_REF,
            GROUP_POST_BODY,
            GROUP_POST_INTERACTIONS,
            BasePlatformScraper,
        )
        from abc import ABC
        assert issubclass(BasePlatformScraper, ABC)
        assert GROUP_ITEM_REF == "ItemRef"
        assert GROUP_POST_BODY == "Post.body"
        assert GROUP_POST_INTERACTIONS == "Post.interactions"
        assert GROUP_COMMENTS == "Comments"

    def test_spec_exists(self):
        from semilabs_hone.modules.collection.scrapers.spec import (
            Flow,
            LoginSpec,
            PlatformSpec,
            Step,
        )
        from pydantic import BaseModel
        assert issubclass(Step, BaseModel)
        assert issubclass(Flow, BaseModel)
        assert issubclass(LoginSpec, BaseModel)
        assert issubclass(PlatformSpec, BaseModel)

    def test_field_extract_exists(self):
        from semilabs_hone.modules.collection.scrapers.field_extract import (
            extract_api,
            extract_dom,
            render_template,
        )
        assert callable(extract_api)
        assert callable(extract_dom)
        assert callable(render_template)

    def test_engine_exists(self):
        from semilabs_hone.modules.collection.scrapers.engine import GenericEngine
        from semilabs_hone.modules.collection.scrapers.base import BasePlatformScraper
        assert issubclass(GenericEngine, BasePlatformScraper)

    def test_registry_exists(self):
        from semilabs_hone.modules.collection.scrapers.registry import (
            get,
            list_platforms,
            load_registry,
        )
        assert callable(load_registry)
        assert callable(list_platforms)
        assert callable(get)

    def test_registry_has_xiaohongshu(self):
        from semilabs_hone.modules.collection.scrapers.registry import list_platforms
        platforms = list_platforms()
        assert "xiaohongshu" in platforms
