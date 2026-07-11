"""Shared fixtures + helpers for PRD §8 BDD acceptance tests.

Mirrors the battle-tested scaffold in tests/collection/test_integration.py:
config-reloading tmp_data_dir, drop-all db_session, and the handler-env
patch helpers so BDD tests never wall-clock or hit a real browser.
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_data_dir(monkeypatch):
    """Isolate data directory: reload config + reset db engine + registry cache."""
    td = Path(tempfile.mkdtemp())
    td.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SEMILABS_DATA_DIR", str(td))

    import config
    importlib.reload(config)

    try:
        import semilabs_hone.core.models.db as db_mod
        db_mod.reset_engine()
    except Exception:
        pass

    try:
        import semilabs_hone.modules.collection.scrapers.registry as reg_mod
        reg_mod._registry_cache = None
    except Exception:
        pass

    db_file = td / "factory.db"
    if db_file.exists():
        db_file.unlink()

    yield td


@pytest.fixture
def db_session(tmp_data_dir):
    """Create tables, yield session, then drop all for isolation."""
    from semilabs_hone.core.models.db import init_db, get_session, reset_engine, get_engine, Base
    init_db()
    sess = get_session()
    try:
        yield sess
    finally:
        sess.close()
        engine = get_engine()
        Base.metadata.drop_all(engine)
        reset_engine()


# ---------------------------------------------------------------------------
# Handler-env helpers (no wall-clock, no real browser)
# ---------------------------------------------------------------------------

async def _noop_async(*args, **kwargs):
    return None


def _patch_handler_env(h_mod, mock_engine):
    """Swap engine/rhythm/night-sleep hooks for a mock; return originals."""
    orig = {
        "engine": h_mod._get_engine,
        "rhythm": h_mod._check_rhythm,
        "night": h_mod._night_sleep_if_quiet,
    }
    h_mod._get_engine = lambda platform, account_id, progress_cb: mock_engine
    h_mod._check_rhythm = lambda account_id, progress_cb: None
    h_mod._night_sleep_if_quiet = _noop_async
    return orig


def _restore_handler_env(h_mod, orig):
    h_mod._get_engine = orig["engine"]
    h_mod._check_rhythm = orig["rhythm"]
    h_mod._night_sleep_if_quiet = orig["night"]


def _make_task(db_session, *, status="running", max_posts=10, platform="xiaohongshu"):
    from semilabs_hone.core.models.task import CollectionTask
    task = CollectionTask(account_id=1, platform=platform,
                          status=status, max_posts_per_keyword=max_posts)
    db_session.add(task)
    db_session.commit()
    return task.id


async def _async_return(value):
    return value
