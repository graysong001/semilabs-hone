"""Registry loading of the zhihu platform.yaml (T27 🟡).

🟡 note: this only validates the yaml PARSES against PlatformSpec and the
risk_tier/captcha_policy defaults are correct. The actual search/detail/comments
JSON-path maps are produced from known zhihu API endpoints and MUST be
human-verified against a real recording (T27 人验). These tests do NOT assert
field-path correctness.
"""
from __future__ import annotations

import pytest

from semilabs_hone.modules.collection.scrapers.registry import get, list_platforms
from semilabs_hone.modules.collection.scrapers.spec import PlatformSpec


class TestZhihuRegistryLoad:
    def test_zhihu_listed_in_registry(self):
        assert "zhihu" in list_platforms()

    def test_zhihu_spec_loads_and_fields(self):
        spec, adapter = get("zhihu")
        assert isinstance(spec, PlatformSpec)
        assert spec.platform == "zhihu"
        assert spec.display_name  # non-empty
        assert spec.base_url.startswith("https://")

    def test_zhihu_has_three_flows(self):
        spec, _ = get("zhihu")
        for flow in ("search", "detail", "comments"):
            assert flow in spec.flows, f"zhihu missing flow: {flow}"
            assert len(spec.flows[flow].steps) > 0, f"zhihu {flow} flow has no steps"

    def test_zhihu_captcha_defaults_account_manual(self):
        """契约§5: zhihu 是 account 站，默认 manual 立即转人工。"""
        spec, _ = get("zhihu")
        assert spec.risk_tier == "account"
        assert spec.captcha_policy == "manual"

    def test_zhihu_search_flow_has_navigate_and_extract(self):
        spec, _ = get("zhihu")
        step_types = [s.type for s in spec.flows["search"].steps]
        assert "navigate" in step_types
        assert "extract" in step_types
