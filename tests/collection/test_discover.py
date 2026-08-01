"""Platform Discover tests — XHR filter/classify, DOM container, routes, helpers.

Covers:
- _discover_filter_and_classify_xhr: XHR filtering and classification logic
- _discover_identify_dom_containers: DOM container detection with real HTML
- _discover_contains_array: helper for detecting arrays in nested JSON
- Discover routes: GET /discover, POST /api/discover/start
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import semilabs_hone.modules.collection.handlers as h_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_data_dir):
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def real_html():
    """Load the real XHS search HTML fixture."""
    path = Path(__file__).resolve().parent.parent.parent / "docs" / "xhs pages" / "AI coding - 小红书搜索.html"
    if not path.exists():
        pytest.skip(f"Real HTML fixture not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. XHR 过滤与分类测试
# ---------------------------------------------------------------------------

class TestDiscoverFilterAndClassifyXhr:
    """Test _discover_filter_and_classify_xhr: static/tracking exclusion + category."""

    def _make_xhr(self, url: str, data, method: str = "GET") -> dict:
        return {"url": url, "method": method, "data": data}

    def test_mixed_xhr_list(self):
        """10 条混合 XHR: 3 数据 API, 2 埋点, 3 静态, 2 配置 → 排除静态和埋点。"""
        # 3 data APIs — large JSON containing arrays (must be > 1024 bytes)
        data_api_payload = {"data": {"notes": [{"id": i, "title": f"note_{i}_" + "x" * 30} for i in range(30)]}}
        data_apis = [
            self._make_xhr("https://edith.xiaohongshu.com/api/sns/web/v1/search/notes?keyword=test", data_api_payload),
            self._make_xhr("https://edith.xiaohongshu.com/api/sns/web/v2/search/notes?page=2", data_api_payload),
            self._make_xhr("https://edith.xiaohongshu.com/api/sns/web/v1/search/recommend", data_api_payload),
        ]

        # 2 tracking/analytics — URL contains collect/track patterns
        tracking = [
            self._make_xhr("https://fe-video-qc.xhscdn.com/collect/v2/event", {"events": []}),
            self._make_xhr("https://trk.xiaohongshu.com/track/general?t=1234", {"type": "click"}),
        ]

        # 3 static resources — .css/.js/.png extensions
        statics = [
            self._make_xhr("https://cdn.example.com/assets/main.css", {}),
            self._make_xhr("https://cdn.example.com/bundle.js", {}),
            self._make_xhr("https://img.example.com/cover.png", {}),
        ]

        # 2 config APIs — small JSON without arrays
        config_payload_1 = {"version": "2.0", "feature_flags": {"dark_mode": True, "new_search": False}, "config_key": "abc" * 100}
        config_payload_2 = {"settings": {"page_size": 20, "timeout": 3000}, "app_id": "xhs_web", "extra": "x" * 200}
        configs = [
            self._make_xhr("https://edith.xiaohongshu.com/api/config/settings", config_payload_1),
            self._make_xhr("https://edith.xiaohongshu.com/api/user/preferences", config_payload_2),
        ]

        xhr_list = data_apis + tracking + statics + configs
        results = h_mod._discover_filter_and_classify_xhr(xhr_list)

        # Should exclude statics (3) and tracking (2) → keep 5
        assert len(results) == 5, f"Expected 5 results, got {len(results)}: {[r['url'] for r in results]}"

        # Separate by category
        categories = {r["url"]: r["category"] for r in results}

        # Data APIs should be "list_data" or "data_api" (URL contains "search")
        for api in data_apis:
            url = api["url"]
            assert url in categories, f"Data API {url} should not be filtered"
            assert categories[url] in ("list_data", "data_api"), \
                f"Data API {url} category={categories[url]}, expected list_data or data_api"

        # Config APIs should be "config" or "other"
        for cfg in configs:
            url = cfg["url"]
            assert url in categories, f"Config API {url} should not be filtered"
            assert categories[url] in ("config", "other"), \
                f"Config {url} category={categories[url]}, expected config or other"

        # Static/tracking should NOT appear
        for s in statics + tracking:
            assert s["url"] not in categories, f"Should be excluded: {s['url']}"

    def test_empty_list(self):
        assert h_mod._discover_filter_and_classify_xhr([]) == []

    def test_all_static_returns_empty(self):
        xhr = [
            self._make_xhr("https://cdn.example.com/a.css", {}),
            self._make_xhr("https://cdn.example.com/b.js", {}),
        ]
        assert h_mod._discover_filter_and_classify_xhr(xhr) == []

    def test_list_response_classified(self):
        """Top-level list response > 512 bytes → data_api."""
        big_list = [{"id": i, "text": "x" * 50} for i in range(20)]
        xhr = [self._make_xhr("https://api.example.com/feed", big_list)]
        results = h_mod._discover_filter_and_classify_xhr(xhr)
        assert len(results) == 1
        assert results[0]["category"] == "data_api"


# ---------------------------------------------------------------------------
# 2. DOM 容器识别测试（用真实 HTML）
# ---------------------------------------------------------------------------

class TestDiscoverIdentifyDomContainers:
    """Test _discover_identify_dom_containers with real XHS HTML."""

    def test_real_html_identifies_containers(self, real_html):
        containers = h_mod._discover_identify_dom_containers(real_html)

        # At least 1 container found
        assert len(containers) >= 1, "Should identify at least 1 container"

        # Find data-note-id container
        note_containers = [c for c in containers if "data-note-id" in c["selector"]]
        assert len(note_containers) >= 1, \
            f"Expected container with data-note-id selector, got: {[c['selector'] for c in containers]}"

        # item_count >= 20
        note_c = note_containers[0]
        assert note_c["item_count"] >= 20, \
            f"Expected item_count >= 20, got {note_c['item_count']}"

        # sample_fields non-empty (at least title or author)
        assert note_c["sample_fields"], "sample_fields should not be empty"
        assert any(k in note_c["sample_fields"] for k in ("title", "author")), \
            f"sample_fields should contain title or author, got: {note_c['sample_fields']}"

    def test_empty_html_returns_empty(self):
        assert h_mod._discover_identify_dom_containers("") == []

    def test_no_containers_html(self):
        html = "<html><body><p>No containers here</p></body></html>"
        assert h_mod._discover_identify_dom_containers(html) == []

    def test_fallback_used_without_selectolax(self, real_html, monkeypatch):
        """When selectolax is unavailable, fallback regex still finds containers."""
        import semilabs_hone.modules.collection.handlers as hmod_local

        original_fn = hmod_local._discover_identify_dom_containers

        def _patched(html):
            # Force the fallback path
            return hmod_local._discover_identify_dom_containers_fallback(html)

        monkeypatch.setattr(hmod_local, "_discover_identify_dom_containers", _patched)
        containers = hmod_local._discover_identify_dom_containers(real_html)
        # Fallback should find at least data-note-id
        note_containers = [c for c in containers if "data-note-id" in c["selector"]]
        assert len(note_containers) >= 1


# ---------------------------------------------------------------------------
# 3. 路由测试
# ---------------------------------------------------------------------------

class TestDiscoverRoutes:
    """Test discover page and API endpoints."""

    def test_get_discover_page(self, client):
        """GET /discover → 200, contains '平台探测器'."""
        resp = client.get("/discover")
        assert resp.status_code == 200
        assert "平台探测器" in resp.text

    def test_post_discover_start_valid(self, client, monkeypatch):
        """POST /api/discover/start with valid body → {ok: true, request_id: str}."""
        # Mock IPC client to avoid real file writes
        mock_client_instance = MagicMock()
        mock_ipc_request_cls = MagicMock()

        def _mock_ipc_client():
            return mock_client_instance.__class__, mock_ipc_request_cls

        from semilabs_hone.modules.collection.routes import discover as disc_mod
        monkeypatch.setattr(disc_mod, "_ipc_client", _mock_ipc_client)

        # Mock the IPCClient().submit call
        mock_client_instance.__class__ = MagicMock
        with patch.object(disc_mod, "_ipc_client") as m_ipc:
            mock_cls = MagicMock()
            mock_cls_instance = MagicMock()
            mock_cls.return_value = mock_cls_instance
            m_ipc.return_value = (mock_cls, MagicMock())

            # Also mock _ensure_worker
            monkeypatch.setattr(disc_mod, "_ensure_worker", lambda req: None)

            resp = client.post("/api/discover/start", json={
                "target_url": "https://www.xiaohongshu.com/search_result?keyword=AI",
                "platform_name": "xiaohongshu",
                "flow_type": "search",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "request_id" in data
        assert isinstance(data["request_id"], str)
        assert len(data["request_id"]) > 0

    def test_post_discover_start_missing_url(self, client):
        """POST /api/discover/start without target_url → 400."""
        resp = client.post("/api/discover/start", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_post_discover_start_invalid_url(self, client):
        """POST /api/discover/start with non-HTTP URL → 400."""
        resp = client.post("/api/discover/start", json={
            "target_url": "ftp://example.com/data"
        })
        assert resp.status_code == 400

    def test_post_discover_start_invalid_json(self, client):
        """POST /api/discover/start with invalid JSON → 400."""
        resp = client.post(
            "/api/discover/start",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. _discover_contains_array 辅助函数测试
# ---------------------------------------------------------------------------

class TestDiscoverContainsArray:
    """Test _discover_contains_array helper."""

    def test_top_level_array(self):
        assert h_mod._discover_contains_array({"items": [1, 2, 3]}) is True

    def test_empty_array_returns_false(self):
        """Empty arrays (no actual data) → False."""
        assert h_mod._discover_contains_array({"data": {"notes": []}}) is False

    def test_no_array_returns_false(self):
        assert h_mod._discover_contains_array({"config": "value"}) is False

    def test_deep_nested_array(self):
        assert h_mod._discover_contains_array(
            {"deep": {"nested": {"list": [{"a": 1}]}}}
        ) is True

    def test_depth_limit_exceeded(self):
        """Beyond depth 3, returns False even with arrays."""
        # depth 4 nesting — should not be found
        obj = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        # depth: a=1, b=2, c=3, d=4 → exceeds max depth of 3
        assert h_mod._discover_contains_array(obj) is False

    def test_top_level_list(self):
        """A non-empty top-level list → True."""
        assert h_mod._discover_contains_array([1, 2, 3]) is True

    def test_empty_top_level_list(self):
        """An empty top-level list → False."""
        assert h_mod._discover_contains_array([]) is False
