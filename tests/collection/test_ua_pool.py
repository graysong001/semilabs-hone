"""UA pool tests — pure logic + mocked browser/httpx (PRD §4.2.3 anti-detect).

Covers _get_chrome_major_version, get_ua strategy dispatch, _get_real_ua,
_get_variety_ua (cache hit / remote / bundled fallback), _load_pool_cache TTL,
_save_pool_cache, _fetch_remote_uas (JSON / newline / dict shapes).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from semilabs_hone.modules.collection.anti_detect import ua_pool
from semilabs_hone.modules.collection.anti_detect.ua_pool import (
    _BUNDLED_UAS,
    _fetch_remote_uas,
    _get_chrome_major_version,
    _get_pool_file,
    _get_real_ua,
    _get_variety_ua,
    _load_pool_cache,
    _save_pool_cache,
    get_ua,
)


UA120 = _BUNDLED_UAS[0]  # Chrome 120


# ─── _get_chrome_major_version ───────────────────────────────────────────

class TestChromeMajorVersion:
    def test_extracts_major(self):
        assert _get_chrome_major_version(UA120) == 120

    def test_none_when_no_match(self):
        assert _get_chrome_major_version("Mozilla/5.0 Firefox/115") is None

    def test_none_empty(self):
        assert _get_chrome_major_version("") is None


# ─── _get_pool_file ──────────────────────────────────────────────────────

def test_pool_file_under_data_dir(tmp_data_dir):
    f = _get_pool_file()
    assert f.name == "ua_pool.json"
    assert "collection" in str(f)


# ─── get_ua strategy dispatch ────────────────────────────────────────────

class TestGetUaDispatch:
    async def test_real_strategy_reads_browser(self, monkeypatch):
        called = {"real": False}

        async def fake_real(ctx):
            called["real"] = True
            return UA120

        monkeypatch.setattr(ua_pool, "_get_real_ua", fake_real)
        import config
        monkeypatch.setattr(config, "UA_STRATEGY", "real")

        ua = await get_ua(MagicMock())
        assert ua == UA120
        assert called["real"] is True

    async def test_variety_strategy_dispatches(self, monkeypatch):
        called = {"var": False}

        async def fake_var(ctx):
            called["var"] = True
            return UA120

        monkeypatch.setattr(ua_pool, "_get_variety_ua", fake_var)
        import config
        monkeypatch.setattr(config, "UA_STRATEGY", "variety")

        ua = await get_ua(MagicMock())
        assert ua == UA120
        assert called["var"] is True


# ─── _get_real_ua ─────────────────────────────────────────────────────────

class TestGetRealUa:
    async def test_reads_navigator_useragent_and_closes_page(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=UA120)
        page.close = AsyncMock()
        ctx = MagicMock()
        ctx.new_page = AsyncMock(return_value=page)

        ua = await _get_real_ua(ctx)
        assert ua == UA120
        page.evaluate.assert_called_once_with("navigator.userAgent")
        page.close.assert_called_once()


# ─── _load_pool_cache / _save_pool_cache ────────────────────────────────

class TestPoolCache:
    def test_missing_file_returns_none(self, tmp_path):
        assert _load_pool_cache(tmp_path / "nope.json") is None

    def test_valid_unexpired_cache_loaded(self, tmp_path):
        f = tmp_path / "pool.json"
        f.write_text(json.dumps({"uas": [UA120], "fetched_at": time.time()}))
        data = _load_pool_cache(f)
        assert data is not None
        assert data["uas"] == [UA120]

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "UA_POOL_TTL", 60)
        f = tmp_path / "pool.json"
        f.write_text(json.dumps({"uas": [UA120], "fetched_at": 0}))
        assert _load_pool_cache(f) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        f = tmp_path / "pool.json"
        f.write_text("{not json")
        assert _load_pool_cache(f) is None

    def test_save_creates_parents_and_roundtrip(self, tmp_path):
        f = tmp_path / "nested" / "pool.json"
        _save_pool_cache(f, [UA120])
        assert f.exists()
        data = _load_pool_cache(f)
        assert data["uas"] == [UA120]


# ─── _get_variety_ua ──────────────────────────────────────────────────────

def _ctx_returning(ua: str):
    """A fake BrowserContext whose new_page evaluates to `ua`."""
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=ua)
    page.close = AsyncMock()
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    return ctx


class TestGetVarietyUa:
    async def test_cache_hit_returns_matching(self, monkeypatch, tmp_path):
        # Cache has a Chrome-120 UA; browser reports Chrome 120.
        pool_file = tmp_path / "pool.json"
        _save_pool_cache(pool_file, [UA120])
        monkeypatch.setattr(ua_pool, "_get_pool_file", lambda: pool_file)

        ua = await _get_variety_ua(_ctx_returning(UA120))
        assert _get_chrome_major_version(ua) == 120

    async def test_remote_fetch_used_on_cache_miss(self, monkeypatch, tmp_path):
        pool_file = tmp_path / "pool.json"
        monkeypatch.setattr(ua_pool, "_get_pool_file", lambda: pool_file)

        import config
        monkeypatch.setattr(config, "UA_REMOTE_URL", "https://example.com/uas")

        async def fake_fetch(url):
            assert url == "https://example.com/uas"
            return [UA120]

        monkeypatch.setattr(ua_pool, "_fetch_remote_uas", fake_fetch)

        ua = await _get_variety_ua(_ctx_returning(UA120))
        assert _get_chrome_major_version(ua) == 120
        # Cached for next time.
        assert _load_pool_cache(pool_file) is not None

    async def test_offline_falls_back_to_bundled(self, monkeypatch, tmp_path):
        pool_file = tmp_path / "pool.json"
        monkeypatch.setattr(ua_pool, "_get_pool_file", lambda: pool_file)
        import config
        monkeypatch.setattr(config, "UA_REMOTE_URL", None)

        ua = await _get_variety_ua(_ctx_returning(UA120))
        assert ua in _BUNDLED_UAS

    async def test_no_chrome_major_returns_real_ua(self, monkeypatch, tmp_path):
        # Browser UA has no Chrome/ marker → can't filter → return real UA.
        pool_file = tmp_path / "pool.json"
        monkeypatch.setattr(ua_pool, "_get_pool_file", lambda: pool_file)
        import config
        monkeypatch.setattr(config, "UA_REMOTE_URL", None)
        weird = "Mozilla/5.0 Firefox/115"
        ua = await _get_variety_ua(_ctx_returning(weird))
        assert ua == weird


# ─── _fetch_remote_uas ───────────────────────────────────────────────────

class TestFetchRemoteUas:
    async def test_json_list_shape(self, monkeypatch):
        class _Resp:
            def __init__(self, data, text):
                self._data = data
                self.text = text

            def json(self):
                return self._data

            def raise_for_status(self):
                pass

        class _Client:
            def __init__(self, resp):
                self._resp = resp

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url):
                return self._resp

        import httpx
        uas = [UA120, "Mozilla/5.0 Firefox/115"]
        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _Client(_Resp(uas, "")))
        result = await _fetch_remote_uas("https://x")
        assert result == uas

    async def test_newline_shape(self, monkeypatch):
        class _Resp:
            text = f"{UA120}\n\nMozilla/5.0 Firefox/115\n"

            def json(self):
                raise ValueError("not json")

            def raise_for_status(self):
                pass

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url):
                return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _Client())
        result = await _fetch_remote_uas("https://x")
        assert UA120 in result
        assert "Mozilla/5.0 Firefox/115" in result

    async def test_dict_with_uas_key(self, monkeypatch):
        class _Resp:
            def json(self):
                return {"uas": [UA120]}

            text = ""

            def raise_for_status(self):
                pass

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url):
                return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _Client())
        result = await _fetch_remote_uas("https://x")
        assert result == [UA120]
