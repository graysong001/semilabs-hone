"""Validate API extraction against real saved XHS search notes response.

This integration test uses the real JSON response saved at docs/xhs pages/
to verify that the JSONPath field map configured in platform.yaml correctly
extracts note data from the search API response.  No mocks.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from semilabs_hone.modules.collection.scrapers.field_extract import extract_api

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
RESPONSE_PATH = PROJECT_ROOT / "docs" / "xhs pages" / "search notes response.json"
PLATFORM_YAML = (
    PROJECT_ROOT
    / "semilabs_hone"
    / "modules"
    / "collection"
    / "scrapers"
    / "platforms"
    / "xiaohongshu"
    / "platform.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_response() -> dict:
    assert RESPONSE_PATH.exists(), f"Response fixture not found: {RESPONSE_PATH}"
    return json.loads(RESPONSE_PATH.read_text(encoding="utf-8"))


def _load_search_field_map() -> tuple[str, dict[str, str]]:
    """Load group and field_map from platform.yaml search scroll_collect step."""
    assert PLATFORM_YAML.exists(), f"platform.yaml not found: {PLATFORM_YAML}"
    cfg = yaml.safe_load(PLATFORM_YAML.read_text(encoding="utf-8"))
    steps = cfg["flows"]["search"]["steps"]
    scroll_step = next(s for s in steps if s.get("type") == "scroll_collect")
    return scroll_step["group"], scroll_step["map"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def extraction_results() -> list[dict]:
    """Run API extraction once and cache for all tests."""
    response = _load_response()
    group, field_map = _load_search_field_map()
    return extract_api(response, group, field_map)


class TestApiResponseReal:
    """Integration tests: platform.yaml JSONPaths vs real XHS search API response."""

    def test_non_empty_results(self, extraction_results: list[dict]):
        """Should extract at least one item from the response."""
        assert len(extraction_results) > 0, "extract_api returned no items"

    def test_minimum_result_count(self, extraction_results: list[dict]):
        """Real response has multiple items; expect at least 5."""
        assert len(extraction_results) >= 5, (
            f"Expected >= 5 results, got {len(extraction_results)}"
        )

    def test_item_id_present(self, extraction_results: list[dict]):
        """Every result must have a non-empty item_id (note ID)."""
        for i, row in enumerate(extraction_results):
            assert row.get("item_id"), (
                f"Result #{i} has empty/missing item_id: {row}"
            )

    def test_item_id_hex_format(self, extraction_results: list[dict]):
        """item_id should be hex string (XHS note_id format: 24-char hex).

        Allow some non-hex IDs (ads/promoted content use UUID#timestamp format).
        """
        import re
        hex_pattern = re.compile(r"^[a-f0-9]+$")
        hex_count = sum(
            1
            for r in extraction_results
            if r.get("item_id") and hex_pattern.match(r["item_id"])
        )
        # Allow some non-hex IDs (ads/promoted content)
        ratio = hex_count / len(extraction_results)
        assert ratio >= 0.7, (
            f"Only {ratio:.0%} of item_ids are hex format "
            f"({hex_count}/{len(extraction_results)})"
        )

    def test_title_present(self, extraction_results: list[dict]):
        """At least 80% of results should have a non-empty title."""
        with_title = [r for r in extraction_results if r.get("title")]
        ratio = len(with_title) / len(extraction_results)
        assert ratio >= 0.8, (
            f"Only {ratio:.0%} results have title "
            f"({len(with_title)}/{len(extraction_results)})"
        )

    def test_author_name_present(self, extraction_results: list[dict]):
        """At least 80% of results should have author_name."""
        with_author = [r for r in extraction_results if r.get("author_name")]
        ratio = len(with_author) / len(extraction_results)
        assert ratio >= 0.8, (
            f"Only {ratio:.0%} results have author_name "
            f"({len(with_author)}/{len(extraction_results)})"
        )

    def test_likes_present(self, extraction_results: list[dict]):
        """At least 70% of results should have likes."""
        with_likes = [r for r in extraction_results if r.get("likes") is not None]
        ratio = len(with_likes) / len(extraction_results)
        assert ratio >= 0.7, (
            f"Only {ratio:.0%} results have likes "
            f"({len(with_likes)}/{len(extraction_results)})"
        )

    def test_first_item_values(self, extraction_results: list[dict]):
        """Spot-check first item against known values from the fixture."""
        first = extraction_results[0]
        # First item in the fixture: id=686faf8d000000001202f687
        assert first["item_id"] == "686faf8d000000001202f687"
        assert first["title"] == "为什么 AI Coding 这么火？"
        assert first["author_name"] == "Koji杨远骋"
        assert first["likes"] == "261"

    def test_no_duplicate_item_ids(self, extraction_results: list[dict]):
        """Item_ids should be mostly unique (allow max 2 dupes from ads)."""
        ids = [r["item_id"] for r in extraction_results if r.get("item_id")]
        unique_ids = set(ids)
        dupe_count = len(ids) - len(unique_ids)
        # Allow up to 2 duplicates from ads/promoted content
        assert dupe_count <= 2, (
            f"Found {dupe_count} duplicate item_ids (max allowed: 2)"
        )
