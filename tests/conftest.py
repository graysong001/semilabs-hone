"""公共 fixtures。惰性 import 未建模块, 避免 collection 失败。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """重定向 data/ 到 tmp_path, 隔离 DB/IPC/日志/图片。"""
    data = tmp_path / "data"
    for sub in ["logs", "ipc/requests", "ipc/results", "ipc/progress",
                "ipc/control/cancel", "collection/images", "collection/profiles",
                "collection/exports", "collection/debug"]:
        (data / sub).mkdir(parents=True, exist_ok=True)
    try:
        import config  # noqa: F401  (repo 根在 sys.path)
        monkeypatch.setattr(config, "DATA_DIR", data, raising=False)
        monkeypatch.setattr(config, "DB_PATH", data / "factory.db", raising=False)
        monkeypatch.setattr(config, "DB_URL", f"sqlite:///{data}/factory.db", raising=False)
        monkeypatch.setattr(config, "IPC_ROOT", data / "ipc", raising=False)
    except Exception:
        pass
    return data


@pytest.fixture
def db_session(tmp_data_dir):
    """临时 SQLite 会话; db 模块未建时 importorskip 本测试。"""
    db = pytest.importorskip("semilabs_hone.core.models.db")
    db.reset_engine()  # clear any cached engine from prior imports
    db.init_db()
    return db.get_session()


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_worker_launch(monkeypatch):
    """禁止测试真拉 Chrome worker; ensure_worker 替换为记录调用的 mock。

    真实实现会 Popen 真 worker（进而拉真 Chrome）, 测试一律走 mock;
    需要断言接线的测试可请求本 fixture 拿 mock（E2E 用 `.original` 取回真函数）。
    """
    try:
        from semilabs_hone.core.ipc import worker_spawner
    except Exception:
        return None
    mock = MagicMock()
    mock.original = worker_spawner.ensure_worker
    monkeypatch.setattr(worker_spawner, "ensure_worker", mock)
    return mock


@pytest.fixture(autouse=True)
def no_rhythm_sleep(monkeypatch):
    """节律延迟全局替换为即时 no-op (FIX_PLAN F4), 防止测试真睡 30-180s。

    需要验证节律接线的契约测试在测试内重新 patch 计数即可。
    预热停留 (warmup_dwell) 同理 —— 预热本身仍真实执行。
    QR 扫码成功轮询同理置零（handlers.QR_POLL_INTERVAL）。
    """
    try:
        from semilabs_hone.modules.collection.scheduler import rhythm
    except Exception:
        return None

    def _make_noop(original):
        async def _noop():
            return None
        _noop.original = original  # 契约测试可取回真函数
        return _noop

    monkeypatch.setattr(rhythm, "note_delay", _make_noop(rhythm.note_delay))
    monkeypatch.setattr(rhythm, "keyword_delay", _make_noop(rhythm.keyword_delay))
    monkeypatch.setattr(rhythm, "warmup_dwell", _make_noop(rhythm.warmup_dwell))
    try:
        from semilabs_hone.modules.collection import handlers
        monkeypatch.setattr(handlers, "QR_POLL_INTERVAL", 0.01, raising=False)
        monkeypatch.setattr(handlers, "QR_LOGIN_TIMEOUT", 0.05, raising=False)
    except Exception:
        pass
    return rhythm


@pytest.fixture(autouse=True)
def fresh_platform_registry():
    """清空平台 registry 缓存, 避免跨测试泄漏。

    registry 同时扫包内 platforms/ 与用户目录 data/collection/platforms/
    (USER_SOP G13); 后者随 tmp_data_dir 变化, 所以缓存必须逐测试重置。
    """
    try:
        from semilabs_hone.modules.collection.scrapers import registry
    except Exception:
        yield
        return
    registry.reset_cache()
    yield
    registry.reset_cache()


@pytest.fixture
def load_fixture(fixtures_dir):
    def _load(name: str):
        import json
        with (fixtures_dir / name).open(encoding="utf-8") as f:
            return json.load(f)
    return _load
