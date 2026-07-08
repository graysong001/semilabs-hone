"""公共 fixtures。惰性 import 未建模块, 避免 collection 失败。"""
import sys
from pathlib import Path

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


@pytest.fixture
def load_fixture(fixtures_dir):
    def _load(name: str):
        import json
        with (fixtures_dir / name).open(encoding="utf-8") as f:
            return json.load(f)
    return _load
