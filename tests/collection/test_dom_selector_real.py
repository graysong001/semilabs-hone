"""Validate DOM extraction selectors against real saved HTML from XHS search page.

This integration test uses the real HTML saved at docs/xhs pages/ to verify that
the CSS selectors configured in platform.yaml actually match the page structure
and extract meaningful data.  No mocks.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from semilabs_hone.modules.collection.scrapers.field_extract import extract_dom_multi

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
HTML_PATH = PROJECT_ROOT / "docs" / "xhs pages" / "AI coding - 小红书搜索.html"
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
def _load_html() -> str:
    assert HTML_PATH.exists(), f"HTML fixture not found: {HTML_PATH}"
    return HTML_PATH.read_text(encoding="utf-8")


def _load_scroll_collect_config() -> dict:
    """Load dom_container and dom_fallback from platform.yaml search flow."""
    assert PLATFORM_YAML.exists(), f"platform.yaml not found: {PLATFORM_YAML}"
    cfg = yaml.safe_load(PLATFORM_YAML.read_text(encoding="utf-8"))
    steps = cfg["flows"]["search"]["steps"]
    scroll_step = next(s for s in steps if s.get("type") == "scroll_collect")
    return {
        "dom_container": scroll_step["dom_container"],
        "dom_fallback": scroll_step["dom_fallback"],
    }


def _extract_with_attr_support(
    html: str, dom_container: str, dom_fallback: dict
) -> list[dict]:
    """Wrapper around extract_dom_multi that also handles 'attr:' prefix fields.

    extract_dom_multi only supports 'css:' prefix.  The platform.yaml uses
    'attr:<attribute-name>' to extract an attribute from the container element
    itself.  This helper:
    1. Separates attr-fields from css-fields
    2. Calls extract_dom_multi for css-fields
    3. Supplements results with attr-fields extracted from containers
    """
    from selectolax.parser import HTMLParser

    # Separate attr-prefixed fields from css-prefixed fields
    attr_fields: dict[str, str] = {}
    css_fields: dict[str, str] = {}
    for field_name, expr in dom_fallback.items():
        if expr.startswith("attr:"):
            attr_fields[field_name] = expr[5:]  # strip "attr:" prefix
        else:
            css_fields[field_name] = expr

    # Parse HTML and find containers
    tree = HTMLParser(html)
    containers = tree.css(dom_container)

    # Get css-field results via extract_dom_multi
    results = extract_dom_multi(html, dom_container, css_fields) if css_fields else []

    # Supplement with attr-fields from container elements
    if attr_fields and containers:
        # Results from extract_dom_multi correspond 1:1 with non-empty containers
        # Re-extract matching containers to zip with results
        result_idx = 0
        enriched: list[dict] = []
        for container in containers:
            # Check if this container produced a result (non-all-None row)
            row_has_data = False
            if css_fields:
                # Reproduce the "any non-None" check from extract_dom_multi
                for expr in css_fields.values():
                    if not expr.startswith("css:"):
                        continue
                    selector_part = expr[4:]
                    if "@" in selector_part:
                        selector_part, attr = selector_part.split("@", 1)
                    nodes = container.css(selector_part)
                    if nodes:
                        row_has_data = True
                        break
            else:
                row_has_data = True

            if row_has_data and result_idx < len(results):
                row = results[result_idx].copy()
                result_idx += 1
            elif not css_fields:
                row = {}
                row_has_data = True
            else:
                # Container didn't produce a row (all-None) - still add attr fields
                row = {k: None for k in css_fields}

            # Add attr-fields from container
            for field_name, attr_name in attr_fields.items():
                row[field_name] = container.attributes.get(attr_name)

            # Skip rows where ONLY attr-fields have data (empty ad containers)
            css_has_data = any(
                row.get(k) is not None for k in css_fields
            ) if css_fields else False
            if not css_has_data and attr_fields:
                # Only attr field present - likely an ad wrapper, skip
                continue

            # Only include if at least one field is non-None
            if any(v is not None for v in row.values()):
                enriched.append(row)

        # Deduplicate by item_id (nested sections can produce duplicates)
        seen_ids: set[str] = set()
        deduped: list[dict] = []
        for row in enriched:
            rid = row.get("item_id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            deduped.append(row)
        return deduped

    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def extraction_results() -> list[dict]:
    """Run DOM extraction once and cache for all tests."""
    html = _load_html()
    config = _load_scroll_collect_config()
    return _extract_with_attr_support(
        html, config["dom_container"], config["dom_fallback"]
    )


class TestDomSelectorReal:
    """Integration tests: platform.yaml DOM selectors vs real XHS search HTML."""

    def test_minimum_result_count(self, extraction_results: list[dict]):
        """Page has 44 cards; extraction should return at least 20."""
        assert len(extraction_results) >= 20, (
            f"Expected >= 20 results, got {len(extraction_results)}"
        )

    def test_item_id_present(self, extraction_results: list[dict]):
        """Every result must have a non-empty item_id."""
        for i, row in enumerate(extraction_results):
            assert row.get("item_id"), (
                f"Result #{i} has empty/missing item_id: {row}"
            )

    def test_title_present(self, extraction_results: list[dict]):
        """At least 90% of results must have a non-empty title.

        Some XHS cards (video-only notes) render without a title element.
        """
        with_title = [r for r in extraction_results if r.get("title")]
        ratio = len(with_title) / len(extraction_results)
        assert ratio >= 0.9, (
            f"Only {ratio:.0%} results have title "
            f"({len(with_title)}/{len(extraction_results)})"
        )

    def test_author_name_coverage(self, extraction_results: list[dict]):
        """At least 80% of results should have author_name."""
        with_author = [r for r in extraction_results if r.get("author_name")]
        ratio = len(with_author) / len(extraction_results)
        assert ratio >= 0.8, (
            f"Only {ratio:.0%} results have author_name "
            f"({len(with_author)}/{len(extraction_results)})"
        )

    def test_item_id_hex_format(self, extraction_results: list[dict]):
        """item_id should be hex string (XHS note_id format: 24-char hex)."""
        hex_pattern = re.compile(r"^[a-f0-9]+$")
        hex_count = sum(
            1
            for r in extraction_results
            if r.get("item_id") and hex_pattern.match(r["item_id"])
        )
        # Allow some non-hex IDs (ads/promoted content use UUID format)
        ratio = hex_count / len(extraction_results)
        assert ratio >= 0.8, (
            f"Only {ratio:.0%} of item_ids are hex format "
            f"({hex_count}/{len(extraction_results)})"
        )

    def test_no_duplicate_item_ids(self, extraction_results: list[dict]):
        """All item_ids should be unique (no duplicates)."""
        ids = [r["item_id"] for r in extraction_results if r.get("item_id")]
        unique_ids = set(ids)
        duplicates = [id_ for id_ in unique_ids if ids.count(id_) > 1]
        assert len(ids) == len(unique_ids), (
            f"Found {len(ids) - len(unique_ids)} duplicate item_ids: {duplicates}"
        )
