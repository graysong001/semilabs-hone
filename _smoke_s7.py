"""Stage 7 acceptance smoke: dark pages + vendor assets + task flow.

Isolated tmp DATA_DIR/DB (四件套 patch + reset_engine), dev DB untouched.
"""
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="smoke_s7_"))

import config  # noqa: E402

config.DATA_DIR = tmp
config.DB_PATH = tmp / "factory.db"
config.DB_URL = f"sqlite:///{config.DB_PATH}"
config.IPC_ROOT = tmp / "ipc"

from semilabs_hone.core.models import db as db_mod  # noqa: E402

db_mod.reset_engine()

from fastapi.testclient import TestClient  # noqa: E402

from semilabs_hone.core.ui.app import create_app  # noqa: E402

# NOTE: module routers register in the startup event — TestClient must be a
# context manager (same as tests' client fixtures), otherwise /accounts etc 404.
with TestClient(create_app()) as client:

    # 1) vendor 资产 200
    for path in ("/static/vendor/tailwind.js", "/static/vendor/fonts/fonts.css",
                 "/static/app.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert len(r.content) > 100, f"{path} suspiciously small"
    print("OK vendor assets 200")

    # 2) 三大暗色页面 200 + sidebar/active 标记
    r = client.get("/")
    assert r.status_code == 200 and "任务大厅" in r.text
    assert "bg-app" in r.text and "/static/vendor/tailwind.js" in r.text
    r = client.get("/accounts")
    assert r.status_code == 200 and "bg-app" in r.text
    r = client.get("/posts")
    assert r.status_code == 200 and "bg-app" in r.text
    print("OK / /accounts /posts dark pages")

    # 3) /tasks 302 → /
    r = client.get("/tasks", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    print("OK /tasks 302 -> /")

    # 4) 建任务 → dashboard 行渲染 → pause/resume/delete API
    # (建任务要求该平台有 active 账号 — 先 seed)
    from semilabs_hone.core.models.db import get_session
    from semilabs_hone.core.models.account import Account
    sess = get_session()
    try:
        sess.add(Account(platform="xiaohongshu", remark="smoke", status="active"))
        sess.commit()
    finally:
        sess.close()

    r = client.post("/api/tasks", data={
        "platform": "xiaohongshu", "task_type": "keyword_search",
        "target_value": "smoke", "expected_count": "5",
    })
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    r = client.get(f"/api/tasks/{tid}/row")
    assert r.status_code == 200 and "smoke" in r.text

    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.post(f"/api/tasks/{tid}/resume")
    assert r.status_code == 200 and r.json()["ok"] is True

    r = client.get("/")
    assert "smoke" in r.text

    # delete 拒绝 pending/running — 先 pause 回 paused 再删
    r = client.post(f"/api/tasks/{tid}/pause")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    print("OK task lifecycle (create/row/pause/resume/dashboard/delete)")

    # 5) accounts 三标识 JSON + posts 页空态
    r = client.get("/api/accounts")
    assert r.status_code == 200 and isinstance(r.json(), list)
    r = client.get("/posts")
    assert r.status_code == 200
    print("OK accounts JSON + posts empty state")

print("SMOKE S7 ALL PASS")
