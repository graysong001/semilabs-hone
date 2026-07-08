"""DM-04 Web shell tests.

Covers:
- GET / returns 200 (dashboard)
- Empty DB shows guidance card
- SkimError global handler -> JSON {error, category, fix_hint}
- WSManager broadcast -> message_buffer
Naming: test_<method>_<scenario>_<expected>
"""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from semilabs_hone.core.ui.ws import WSManager, ws_manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_data_dir):
    """Create app with tmp data dir; reset engine so startup uses patched config."""
    from semilabs_hone.core.models.db import reset_engine

    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    """TestClient (triggers startup -> init_db + setup_logger + manifest scan)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_get_dashboard_returns_200(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200


def test_get_dashboard_empty_shows_guidance(client: TestClient):
    """Empty DB should show the 'no accounts' guidance card."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "开始使用" in html or "尚未添加" in html


# ---------------------------------------------------------------------------
# SkimError global handler
# ---------------------------------------------------------------------------

def test_skimerror_handler_returns_json(client: TestClient):
    """Register a test route that raises SkimError, verify JSON response."""
    from fastapi import APIRouter
    from semilabs_hone.core.utils.retry import SkimError

    test_router = APIRouter()

    @test_router.get("/test_skim_error")
    async def raise_skim_error():
        raise SkimError("test error message", category="test_category", fix_hint="test fix hint")

    # Manually include the test router
    client.app.include_router(test_router)

    response = client.get("/test_skim_error")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "test error message"
    assert body["category"] == "test_category"
    assert body["fix_hint"] == "test fix hint"


def test_non_skimerror_not_handled(tmp_data_dir):
    """Non-SkimError exceptions should NOT be caught by our handler (500)."""
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    app = create_app()
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test_runtime_error")
    async def raise_runtime_error():
        raise RuntimeError("unexpected error")

    app.include_router(test_router)

    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/test_runtime_error")
    # FastAPI default for unhandled errors is 500
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# WSManager
# ---------------------------------------------------------------------------

def test_wsmanager_broadcast_in_buffer():
    """Broadcasting a message should add it to the message buffer."""
    mgr = WSManager()
    msg = {"type": "progress", "message": "test progress"}

    import asyncio
    asyncio.run(mgr.broadcast(msg))

    assert len(mgr.message_buffer) == 1
    assert mgr.message_buffer[0] == msg


def test_wsmanager_buffer_maxlen():
    """Buffer should respect maxlen=50."""
    mgr = WSManager()

    import asyncio
    for i in range(60):
        asyncio.run(mgr.broadcast({"type": "progress", "message": f"msg {i}"}))

    assert len(mgr.message_buffer) == 50
    # Oldest messages should have been evicted
    assert mgr.message_buffer[0]["message"] == "msg 10"


def test_wsmanager_connect_disconnect():
    """Connect adds ws to set, disconnect removes it."""
    mgr = WSManager()
    mock_ws = MagicMock()
    mock_ws.accept = AsyncMock()

    import asyncio

    async def _test():
        await mgr.connect(mock_ws)
        assert mock_ws in mgr.connections
        mock_ws.accept.assert_called_once()

        await mgr.disconnect(mock_ws)
        assert mock_ws not in mgr.connections

    asyncio.run(_test())


def test_wsmanager_connect_replays_buffer():
    """New connections should receive buffered messages."""
    mgr = WSManager()
    mock_ws = MagicMock()
    mock_ws.accept = AsyncMock()
    mock_ws.send_json = AsyncMock()
    # Pre-populate buffer
    mgr.message_buffer.append({"type": "progress", "message": "replayed"})

    import asyncio

    async def _test():
        await mgr.connect(mock_ws)
        assert mock_ws in mgr.connections
        # Should have called send_json for the buffered message
        assert mock_ws.send_json.call_count == 1
        assert mock_ws.send_json.call_args[0][0] == {"type": "progress", "message": "replayed"}

    asyncio.run(_test())


def test_wsmanager_module_singleton():
    """The module-level ws_manager should be a WSManager instance."""
    assert isinstance(ws_manager, WSManager)
    assert isinstance(ws_manager.message_buffer, deque)
    assert ws_manager.message_buffer.maxlen == 50


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def test_manifest_discovers_collection(app):
    """create_app startup should discover the collection module via manifest."""
    # The app has already run startup via TestClient
    from semilabs_hone.core.ui.app import _module_registry
    assert "collection" in _module_registry
    mod = _module_registry["collection"]
    assert mod["name"] == "Skim 采集"
    assert mod["module_id"] == "collection"
    assert mod["worker_entry"] == "semilabs_hone.modules.collection.browser.worker_main"


def test_manifest_empty_routes_no_error(app):
    """Empty ROUTES list should not cause errors during startup."""
    # If we get here without exception, the test passes
    # Verified by the app fixture creation above
    from semilabs_hone.core.ui.app import _module_registry
    assert "collection" in _module_registry
