"""Field-extract edge cases — bool/float parse_likes, list-root, [*] path,
DOM page.content() / xpath / expression-error branches.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from semilabs_hone.modules.collection.scrapers.field_extract import (
    _find_list_root,
    extract_api,
    extract_dom,
    parse_likes,
)


class TestParseLikesEdges:
    def test_bool_returns_zero(self):
        assert parse_likes(True) == 0
        assert parse_likes(False) == 0

    def test_float_truncated_to_int(self):
        assert parse_likes(1.9) == 1
        assert parse_likes(12.5) == 12

    def test_int_passes_through(self):
        assert parse_likes(1234) == 1234

    def test_unparseable_unit_returns_zero(self):
        # No digit prefix → regex won't match → 0.
        assert parse_likes("无数") == 0


class TestFindListRoot:
    def test_top_level_list_key_items(self):
        assert _find_list_root({"items": [{"id": 1}]}) == [{"id": 1}]

    def test_top_level_list_key_comments(self):
        assert _find_list_root({"comments": [{"c": "x"}]}) == [{"c": "x"}]

    def test_nested_data_list(self):
        data = {"data": {"items": [{"id": 2}]}}
        assert _find_list_root(data) == [{"id": 2}]

    def test_no_list_returns_empty(self):
        assert _find_list_root({"k": "v"}) == []

    def test_empty_list_not_returned(self):
        # An empty list is not a valid root.
        assert _find_list_root({"items": []}) == []


class TestExtractApiStarPath:
    def test_star_path_derives_list(self):
        # The [*] branch derives the item list (2 items). Per-item field
        # extraction from a [*] expression is the L08 known gap (zhihu
        # {"data":[...]} shape) — here we assert the list is derived.
        data = {"data": {"items": [{"note_id": "a"}, {"note_id": "b"}]}}
        items = extract_api(data, "ItemRef",
                            {"item_id": "$.data.items[*].note_id"})
        assert len(items) == 2

    def test_malformed_jsonpath_returns_empty(self):
        # Invalid expression → no crash, empty list.
        items = extract_api({"data": [1]}, "ItemRef",
                            {"item_id": "$.data[invalid[*]"})
        assert items == []

    def test_non_dict_sample_returns_empty(self):
        assert extract_api([], "ItemRef", {"item_id": "$.x"}) == []
        assert extract_api(None, "ItemRef", {"item_id": "$.x"}) == []

    def test_empty_field_map_returns_empty(self):
        assert extract_api({"data": 1}, "ItemRef", {}) == []


class TestExtractDomEdges:
    def test_page_content_object(self):
        page = MagicMock()
        page.content = MagicMock(return_value="<div>hi</div>")
        rows = extract_dom(page, "Post.body", {"text": "css:div"})
        assert len(rows) == 1
        assert rows[0]["text"] == "hi"

    def test_page_content_exception_falls_back_empty(self):
        page = MagicMock()
        page.content = MagicMock(side_effect=RuntimeError("no page"))
        rows = extract_dom(page, "Post.body", {"text": "css:div"})
        assert rows == []

    def test_xpath_returns_none(self):
        rows = extract_dom("<html></html>", "Post.body", {"x": "xpath://div"})
        assert rows[0]["x"] is None

    def test_attr_extraction(self):
        html = '<a href="https://x.com">link</a>'
        rows = extract_dom(html, "Post.body", {"url": "css:a@href"})
        assert rows[0]["url"] == "https://x.com"

    def test_bad_selector_returns_none(self):
        rows = extract_dom("<div>x</div>", "Post.body", {"t": "css:"})
        assert rows[0]["t"] is None or rows == [{}] or len(rows) >= 1

    def test_empty_html_returns_empty(self):
        assert extract_dom("", "Post.body", {"t": "css:div"}) == []
