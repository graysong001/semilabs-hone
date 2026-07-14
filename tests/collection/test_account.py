"""[契约变更 2026-07-13 S10] Account 端到端测试。

覆盖：
- T80 模型：remark NOT NULL / platform_user_id+platform_nickname / UNIQUE 去重
  （空值多行并存） / 删 phone+profile_dir
- T81 cookie 路径统一（删 acct_ 前缀 bug）+ add_cookies 调用
- T82 QR 成功检测（success_pattern 轮询）+ cookies 提取 + 回写
- T83 路由：GET /api/accounts (JSON) / GET /{id} / PUT /{id} (改 remark)
  / DELETE 清 profile + 有 running 任务拒绝 / login 去硬编码 platform
- T84 导入表单下拉（模板层由 accounts.html 渲染测试覆盖）
- T85 tasks.account_id FK 绑定
- T86 状态机：成功清零→active / 失败+1 达 5→suspended / 导入冲突拒绝
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_data_dir):
    from semilabs_hone.core.models.db import reset_engine
    reset_engine()
    from semilabs_hone.core.ui.app import create_app
    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def _fake_ipc(monkeypatch):
    from semilabs_hone.modules.collection.routes import accounts as acc
    from semilabs_hone.core.ipc.protocol import IPCRequest

    class _FakeClient:
        def submit(self, req):
            return None

    monkeypatch.setattr(acc, "_ipc_client", lambda: (_FakeClient, IPCRequest))


def _seed_account(db_session, *, platform="xiaohongshu", remark="acc"):
    from semilabs_hone.core.models.account import Account
    a = Account(platform=platform, remark=remark)
    db_session.add(a); db_session.commit()
    return a.id


# ─── T80 模型增强 ──────────────────────────────────────────────────────────

class TestAccountModel:
    def test_remark_is_not_null(self, db_session):
        """remark NOT NULL（用户裁决）。"""
        from semilabs_hone.core.models.account import Account
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            db_session.add(Account(platform="xiaohongshu"))  # 无 remark
            db_session.commit()

    def test_remark_accepts_value(self, db_session):
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="小号A")
        db_session.add(a); db_session.commit()
        assert a.remark == "小号A"

    def test_platform_user_id_and_nickname_columns(self, db_session):
        """新增 platform_user_id / platform_nickname 列。"""
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="r")
        db_session.add(a); db_session.commit()
        a.platform_user_id = "u_123"
        a.platform_nickname = "张三"
        db_session.commit()
        assert a.platform_user_id == "u_123"
        assert a.platform_nickname == "张三"

    def test_unique_platform_user_id(self, db_session):
        """UNIQUE(platform, platform_user_id)：同平台同 user_id 拒绝重复。"""
        from semilabs_hone.core.models.account import Account
        from sqlalchemy.exc import IntegrityError
        a1 = Account(platform="xiaohongshu", remark="a", platform_user_id="u_999")
        a2 = Account(platform="xiaohongshu", remark="b", platform_user_id="u_999")
        db_session.add(a1); db_session.commit()
        db_session.add(a2)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_unique_cross_platform_allowed(self, db_session):
        """不同平台同 user_id 允许并存（UNIQUE 是复合的）。"""
        from semilabs_hone.core.models.account import Account
        a1 = Account(platform="xiaohongshu", remark="a", platform_user_id="same_id")
        a2 = Account(platform="zhihu", remark="b", platform_user_id="same_id")
        db_session.add(a1); db_session.add(a2); db_session.commit()
        assert a1.id != a2.id

    def test_null_platform_user_id_multiple_allowed(self, db_session):
        """空值不参与唯一约束：多个未登录空壳可并存（SQLite 多 NULL 允许）。"""
        from semilabs_hone.core.models.account import Account
        a1 = Account(platform="xiaohongshu", remark="a")
        a2 = Account(platform="xiaohongshu", remark="b")
        a3 = Account(platform="xiaohongshu", remark="c")
        db_session.add_all([a1, a2, a3]); db_session.commit()
        assert a1.id != a2.id != a3.id

    def test_phone_and_profile_dir_dropped(self):
        """删 phone / profile_dir 列（用户裁决）。"""
        from semilabs_hone.core.models.account import Account
        assert not hasattr(Account, "phone") or True  # 防御：不再使用
        # 显式检查 ORM 列
        col_names = [c.name for c in Account.__table__.columns]
        assert "phone" not in col_names
        assert "profile_dir" not in col_names
        assert "remark" in col_names
        assert "platform_user_id" in col_names
        assert "platform_nickname" in col_names


class TestAccountCreateSchema:
    def test_remark_required(self):
        """AccountCreate.remark 必填（用户裁决）。"""
        from semilabs_hone.core.models.schemas import AccountCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AccountCreate()  # 缺 remark

    def test_remark_accepted(self):
        from semilabs_hone.core.models.schemas import AccountCreate
        ac = AccountCreate(platform="xiaohongshu", remark="小号A")
        assert ac.remark == "小号A"
        assert ac.platform == "xiaohongshu"


# ─── T85 tasks.account_id FK ──────────────────────────────────────────────

class TestTaskAccountFK:
    def test_account_id_is_foreign_key(self, db_session):
        """collection_tasks.account_id 是 ForeignKey(accounts.id)（裁决：规格漂移）。"""
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.account import Account
        fk_cols = [str(fk.target_fullname) for fk in CollectionTask.account_id.property.columns[0].foreign_keys]
        # 至少有一个 FK 指向 accounts.id
        assert any("accounts.id" in fk for fk in fk_cols), f"FK missing, got {fk_cols}"

    def test_task_can_reference_account(self, db_session):
        from semilabs_hone.core.models.task import CollectionTask
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="t")
        db_session.add(a); db_session.commit()
        t = CollectionTask(account_id=a.id, platform="xiaohongshu")
        db_session.add(t); db_session.commit()
        assert t.account_id == a.id


# ─── T81 cookie 路径统一 ──────────────────────────────────────────────────

class TestCookiePathUnified:
    def test_cookie_path_no_acct_prefix(self, tmp_data_dir):
        """路径用 profiles/{account_id}/，不再用 profiles/acct_{account_id}/。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        p = h_mod._cookie_path_for(42)
        assert "acct_42" not in str(p), f"path 不应含 acct_ 前缀: {p}"
        assert str(p).endswith("profiles/42/cookies.json"), f"unexpected path: {p}"

    async def test_import_cookies_persists_to_unified_path(self, tmp_data_dir):
        import semilabs_hone.modules.collection.handlers as h_mod
        cap, out = lambda msgs: (lambda m, d=None: msgs.append((m, d))), []
        msgs = []
        cap_fn = lambda m, d=None: msgs.append((m, d))
        cookies = [{"name": "sid", "value": "v"}]
        result = await h_mod._import_cookies(100, "xiaohongshu", cookies, cap_fn)
        from config import DATA_DIR
        expected = DATA_DIR / "collection" / "profiles" / "100" / "cookies.json"
        assert expected.exists(), f"应落盘到 {expected}"
        # 不再落到 acct_100
        wrong = DATA_DIR / "collection" / "profiles" / "acct_100" / "cookies.json"
        assert not wrong.exists(), f"不应落到旧路径 {wrong}"
        assert result["ok"] is True


# ─── T82 QR 成功检测 ─────────────────────────────────────────────────────

class TestQRSuccessDetect:
    async def test_qr_login_polls_success_pattern(self, monkeypatch, tmp_data_dir):
        """_do_qr_login 轮询 page.url 匹配 success_pattern（≤timeout）。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        # 模拟 page：url 从 /login 变到 /
        urls = iter(["https://www.xiaohongshu.com/login",
                     "https://www.xiaohongshu.com/login",
                     "https://www.xiaohongshu.com/"])  # 第三次匹配 ^/

        class MockPage:
            url = "https://www.xiaohongshu.com/login"
            def __init__(self):
                self.context = MagicMock()
                self.context.cookies = AsyncMock(return_value=[{"name": "sid", "value": "v"}])
            async def goto(self, url): pass
            async def wait_for_selector(self, *a, **kw): pass
            async def screenshot(self, path): pass
            def __getattribute__(self, name):
                if name == "url":
                    try:
                        return next(urls)
                    except StopIteration:
                        return "https://www.xiaohongshu.com/"
                return object.__getattribute__(self, name)

        mock_page = MockPage()
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))
        # 禁用 identity extract（无 ctx 时自然 None）
        monkeypatch.setattr(h_mod, "_extract_platform_identity",
                            AsyncMock(return_value=None))
        # 禁用 apply_login_success 的 DB 写入（避免 DB session 依赖）
        applied = []
        monkeypatch.setattr(h_mod, "_apply_login_success",
                            lambda *a, **kw: applied.append((a, kw)))
        monkeypatch.setattr(h_mod, "_persist_cookies", lambda aid, c: None)
        # 缩 timeout 让测试快
        import semilabs_hone.modules.collection.scrapers.spec as spec_mod
        # 直接 mock reg_get 让 timeout 小
        from semilabs_hone.modules.collection.scrapers.registry import get as real_get
        def fake_get(platform):
            s, a = real_get(platform)
            # 用 monkeypatch 改 spec.login.timeout
            return s, a
        monkeypatch.setattr(h_mod, "_WORKER_CTX", MagicMock())

        # 让 poll 快速退出
        import asyncio
        orig_sleep = asyncio.sleep
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        msgs = []
        result = await h_mod._do_qr_login("xiaohongshu", 7,
                                          lambda m, d=None: msgs.append((m, d)))
        # 应广播 qr_ready（截图后）
        assert any(m == "qr_ready" for m, _ in msgs)
        # 成功时广播 login_success
        assert any(m == "login_success" for m, _ in msgs) or result.get("login_success")


# ─── T86 状态机 ───────────────────────────────────────────────────────────

class TestAccountStateMachine:
    def test_apply_login_success_clears_fail_count(self, db_session, tmp_data_dir):
        """成功 → 清零 fail_count + active。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="s", fail_count=3, status="inactive")
        db_session.add(a); db_session.commit()
        msgs = []
        h_mod._apply_login_success(a.id, lambda m, d=None: msgs.append((m, d)))
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            acct = sess.query(Account).get(a.id)
            assert acct.status == "active"
            assert acct.fail_count == 0
            assert acct.last_login_at is not None
        finally:
            sess.close()

    def test_apply_login_failure_increments_fail_count(self, db_session, tmp_data_dir):
        """失败 → fail_count+1。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="f", fail_count=0, status="inactive")
        db_session.add(a); db_session.commit()
        h_mod._apply_login_failure(a.id, lambda *a: None)
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            acct = sess.query(Account).get(a.id)
            assert acct.fail_count == 1
        finally:
            sess.close()

    def test_fail_count_reach_five_suspended(self, db_session, tmp_data_dir):
        """连续失败达 5 → suspended。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="5x", fail_count=4, status="inactive")
        db_session.add(a); db_session.commit()
        h_mod._apply_login_failure(a.id, lambda *a: None)
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            acct = sess.query(Account).get(a.id)
            assert acct.status == "suspended"
            assert acct.fail_count == 5
        finally:
            sess.close()

    def test_find_conflicting_account_detects_duplicate(self, db_session, tmp_data_dir):
        """导入 cookie 命中已存在 platform_user_id → 找到冲突 id。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        a1 = Account(platform="xiaohongshu", remark="orig", platform_user_id="dup_user")
        a2 = Account(platform="xiaohongshu", remark="new")
        db_session.add_all([a1, a2]); db_session.commit()
        # a2 导入 cookie 命中 a1 的 user_id
        conflict = h_mod._find_conflicting_account("xiaohongshu", "dup_user", exclude_id=a2.id)
        assert conflict == a1.id

    def test_find_conflicting_allows_same_id(self, db_session, tmp_data_dir):
        """同一账号自己不算冲突。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="self", platform_user_id="u_self")
        db_session.add(a); db_session.commit()
        assert h_mod._find_conflicting_account("xiaohongshu", "u_self", exclude_id=a.id) is None


# ─── T83 路由 ─────────────────────────────────────────────────────────────

class TestAccountRoutes:
    def test_get_accounts_json(self, client, db_session):
        """GET /api/accounts 返回 JSON 列表（供下拉）。"""
        _seed_account(db_session, remark="alpha")
        _seed_account(db_session, remark="beta", platform="zhihu")
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        remarks = {a["remark"] for a in body}
        assert "alpha" in remarks
        assert "beta" in remarks

    def test_get_account_detail(self, client, db_session):
        """GET /api/accounts/{id} 返回 JSON 详情。"""
        aid = _seed_account(db_session, remark="detail")
        resp = client.get(f"/api/accounts/{aid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["remark"] == "detail"

    def test_get_account_detail_404(self, client):
        resp = client.get("/api/accounts/999999")
        assert resp.status_code == 404

    def test_update_remark(self, client, db_session):
        """PUT /api/accounts/{id} 改 remark（仅）。"""
        aid = _seed_account(db_session, remark="old")
        resp = client.put(f"/api/accounts/{aid}", json={"remark": "new"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["remark"] == "new"
        # DB 也更新了
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account
        sess = get_session()
        try:
            a = sess.query(Account).get(aid)
            assert a.remark == "new"
        finally:
            sess.close()

    def test_update_remark_empty_rejected(self, client, db_session):
        """空 remark 拒绝。"""
        aid = _seed_account(db_session, remark="orig")
        resp = client.put(f"/api/accounts/{aid}", json={"remark": ""})
        assert resp.status_code == 400

    def test_delete_with_running_task_rejected(self, client, db_session):
        """有 running 任务→拒绝删除（409）。"""
        from semilabs_hone.core.models.task import CollectionTask
        aid = _seed_account(db_session, remark="busy")
        t = CollectionTask(account_id=aid, platform="xiaohongshu", status="running")
        db_session.add(t); db_session.commit()
        resp = client.delete(f"/api/accounts/{aid}")
        assert resp.status_code == 409
        body = resp.json()
        assert "running" in body["error"].lower() or "任务" in body["error"]

    def test_delete_clears_profile_dir(self, client, db_session, tmp_data_dir):
        """删除账号同时清 profile 目录 + cookies.json。"""
        from config import DATA_DIR
        aid = _seed_account(db_session, remark="cleanup")
        # 造 profile dir + cookies.json
        profile_dir = DATA_DIR / "collection" / "profiles" / str(aid)
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "cookies.json").write_text("[]")
        assert profile_dir.exists()
        resp = client.delete(f"/api/accounts/{aid}")
        assert resp.status_code == 200
        assert not profile_dir.exists(), "profile dir 应被清掉"

    def test_login_uses_account_platform(self, client, db_session, monkeypatch):
        """login 路由 platform 从 Account.platform 读（去硬编码）。"""
        from semilabs_hone.modules.collection.routes import accounts as acc
        from semilabs_hone.core.ipc.protocol import IPCRequest

        captured = {}

        class _FakeClient:
            def submit(self, req):
                captured["platform"] = req.payload["platform"]
                return None

        monkeypatch.setattr(acc, "_ipc_client", lambda: (_FakeClient, IPCRequest))
        # 建一个知乎账号
        from semilabs_hone.core.models.account import Account
        a = Account(platform="zhihu", remark="zh")
        db_session.add(a); db_session.commit()
        resp = client.post(f"/api/accounts/{a.id}/login")
        assert resp.status_code == 200
        assert captured["platform"] == "zhihu", "应从 Account.platform 读，不再硬编码 xiaohongshu"

    def test_validate_uses_account_platform(self, client, db_session, monkeypatch):
        """validate 路由 platform 从 Account.platform 读（去硬编码）。"""
        from semilabs_hone.modules.collection.routes import accounts as acc
        from semilabs_hone.core.ipc.protocol import IPCRequest

        captured = {}

        class _FakeClient:
            def submit(self, req):
                captured["platform"] = req.payload["platform"]
                return None

        monkeypatch.setattr(acc, "_ipc_client", lambda: (_FakeClient, IPCRequest))
        from semilabs_hone.core.models.account import Account
        a = Account(platform="zhihu", remark="zh-v")
        db_session.add(a); db_session.commit()
        resp = client.post(f"/api/accounts/{a.id}/validate")
        assert resp.status_code == 200
        assert captured["platform"] == "zhihu"

    def test_import_cookies_uses_account_platform(self, client, db_session, monkeypatch):
        """import-cookies 路由 platform 从 Account.platform 读（去硬编码）。"""
        from semilabs_hone.modules.collection.routes import accounts as acc
        from semilabs_hone.core.ipc.protocol import IPCRequest

        captured = {}

        class _FakeClient:
            def submit(self, req):
                captured["platform"] = req.payload["platform"]
                return None

        monkeypatch.setattr(acc, "_ipc_client", lambda: (_FakeClient, IPCRequest))
        from semilabs_hone.core.models.account import Account
        a = Account(platform="zhihu", remark="zh-i")
        db_session.add(a); db_session.commit()
        resp = client.post("/api/accounts/import-cookies",
                           data={"account_id": a.id, "cookies": '[{"name":"sid"}]'})
        assert resp.status_code == 200
        assert captured["platform"] == "zhihu"

    def test_edit_dialog_partial(self, client, db_session):
        """GET /api/accounts/{id}/edit 返回 dialog partial HTML。"""
        aid = _seed_account(db_session, remark="edit-me")
        resp = client.get(f"/api/accounts/{aid}/edit")
        assert resp.status_code == 200
        assert "edit-me" in resp.text


# ─── 模板层（accounts.html 下拉） ─────────────────────────────────────────

class TestAccountsTemplate:
    def test_accounts_page_has_edit_button(self, client, db_session):
        """账号列表每行有合并的「编辑」按钮（备注+cookie 统一编辑）。"""
        from semilabs_hone.core.models.account import Account
        a = Account(platform="xiaohongshu", remark="test-acc", status="inactive")
        db_session.add(a); db_session.commit()
        resp = client.get("/accounts")
        assert resp.status_code == 200
        # 列表展示 `备注 (平台昵称)` 双列
        assert "备注 (平台昵称)" in resp.text
        # 每行有合并的「编辑」按钮（指向 /edit）
        assert f'hx-get="/api/accounts/{a.id}/edit"' in resp.text
        # 不再有独立的「编辑备注」「更新Cookie」按钮
        assert "编辑备注" not in resp.text
        assert "更新Cookie" not in resp.text
        # 添加账号表单有可选 cookie 输入框
        assert 'name="cookies"' in resp.text


# ─── S10 补覆盖率：mock _WORKER_CTX 走真 ctx 路径 ─────────────────────────

class TestImportCookiesWithWorkerCtx:
    """[契约变更 2026-07-13 S10] 有 ctx 时 _import_cookies 真注入+验证+回写。"""

    async def test_import_cookies_with_ctx_success(self, db_session, monkeypatch, tmp_data_dir):
        """有 ctx + 验证成功 → 注入+提取身份+active。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account

        a = Account(platform="xiaohongshu", remark="ctx-ok", status="inactive")
        db_session.add(a); db_session.commit()

        # Mock ctx
        mock_ctx = MagicMock()
        mock_ctx.add_cookies = AsyncMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)

        # Mock verify 返回 True
        monkeypatch.setattr(h_mod, "_verify_cookies_on_platform",
                            AsyncMock(return_value=True))
        # Mock identity extract 返回身份
        monkeypatch.setattr(h_mod, "_extract_platform_identity",
                            AsyncMock(return_value={
                                "platform_user_id": "u_777",
                                "platform_nickname": "测试昵称",
                            }))

        msgs = []
        cookies = [{"name": "sid", "value": "v"}]
        result = await h_mod._import_cookies(a.id, "xiaohongshu", cookies,
                                             lambda m, d=None: msgs.append((m, d)))

        assert result["ok"] is True
        assert result["identity"]["platform_user_id"] == "u_777"
        mock_ctx.add_cookies.assert_awaited_once_with(cookies)

        # DB 状态验证
        from semilabs_hone.core.models.db import get_session
        sess = get_session()
        try:
            acct = sess.query(Account).get(a.id)
            assert acct.status == "active"
            assert acct.platform_user_id == "u_777"
            assert acct.platform_nickname == "测试昵称"
            assert acct.fail_count == 0
        finally:
            sess.close()

    async def test_import_cookies_verify_fail_increments_fail_count(self, db_session, monkeypatch, tmp_data_dir):
        """验证失败 → fail_count+1（不 active）。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account

        a = Account(platform="xiaohongshu", remark="ctx-fail", status="inactive", fail_count=0)
        db_session.add(a); db_session.commit()

        mock_ctx = MagicMock()
        mock_ctx.add_cookies = AsyncMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        monkeypatch.setattr(h_mod, "_verify_cookies_on_platform",
                            AsyncMock(return_value=False))

        result = await h_mod._import_cookies(a.id, "xiaohongshu",
                                             [{"name": "bad", "value": "x"}],
                                             lambda *a: None)
        assert result["ok"] is False
        assert "验证失败" in result["reason"]

        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account as AcctModel
        sess = get_session()
        try:
            acct = sess.query(AcctModel).get(a.id)
            assert acct.status == "inactive"
            assert acct.fail_count == 1
        finally:
            sess.close()

    async def test_import_cookies_conflict_rejected(self, db_session, monkeypatch, tmp_data_dir):
        """命中已存在 platform_user_id → 拒绝（conflict 响应）。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account

        a_existing = Account(platform="xiaohongshu", remark="exist",
                             platform_user_id="dup_user")
        a_new = Account(platform="xiaohongshu", remark="new", status="inactive")
        db_session.add_all([a_existing, a_new]); db_session.commit()

        mock_ctx = MagicMock()
        mock_ctx.add_cookies = AsyncMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        monkeypatch.setattr(h_mod, "_verify_cookies_on_platform",
                            AsyncMock(return_value=True))
        # 提取出的 user_id 与 a_existing 冲突
        monkeypatch.setattr(h_mod, "_extract_platform_identity",
                            AsyncMock(return_value={
                                "platform_user_id": "dup_user",
                                "platform_nickname": "撞车",
                            }))

        msgs = []
        result = await h_mod._import_cookies(a_new.id, "xiaohongshu",
                                             [{"name": "sid", "value": "v"}],
                                             lambda m, d=None: msgs.append((m, d)))
        assert result["conflict"] is True
        assert result["existing_id"] == a_existing.id
        assert any(m == "cookie_import_conflict" for m, _ in msgs)

        # a_new 状态保持 inactive（未回写）
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account as AcctModel
        sess = get_session()
        try:
            acct = sess.query(AcctModel).get(a_new.id)
            assert acct.status == "inactive"
            assert acct.platform_user_id is None
        finally:
            sess.close()


class TestVerifyAndIdentityExtract:
    """_verify_cookies_on_platform / _extract_platform_identity mock 测试。"""

    async def test_verify_returns_false_on_fetch_fail(self, monkeypatch):
        """ctx 存在但 page evaluate 抛异常 → False。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        mock_page = MagicMock()
        async def bad_eval(*a, **kw):
            raise RuntimeError("network")
        mock_page.evaluate = bad_eval
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))

        result = await h_mod._verify_cookies_on_platform(mock_ctx, "xiaohongshu")
        assert result is False

    async def test_verify_returns_true_when_no_verify_url(self, monkeypatch):
        """未配 verify_url → 降级为 True（不阻塞）。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)

        # 拿 spec 实例，用 monkeypatch 改属性（monkeypatch 测试结束会自动还原）
        from semilabs_hone.modules.collection.scrapers.registry import get as real_get
        spec, _ = real_get("xiaohongshu")
        monkeypatch.setattr(spec.login, "verify_url", None)
        monkeypatch.setattr(spec, "base_url", "")

        result = await h_mod._verify_cookies_on_platform(mock_ctx, "xiaohongshu")
        assert result is True

    async def test_extract_identity_resolves_dotpath(self, monkeypatch):
        """identity_map 用点分路径解析 JSON。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        mock_page = MagicMock()
        async def good_eval(script, url):
            return {"data": {"user": {"id": 42, "nickname": "小红"}}}
        mock_page.evaluate = good_eval
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))

        result = await h_mod._extract_platform_identity(mock_ctx, "xiaohongshu")
        assert result == {"platform_user_id": "42", "platform_nickname": "小红"}

    async def test_extract_identity_returns_none_when_no_user_id(self, monkeypatch):
        """无 user_id 字段 → 返回 None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        mock_page = MagicMock()
        async def empty_eval(script, url):
            return {"data": {"something": "else"}}
        mock_page.evaluate = empty_eval
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))

        result = await h_mod._extract_platform_identity(mock_ctx, "xiaohongshu")
        assert result is None

    async def test_verify_returns_false_when_page_is_none(self, monkeypatch):
        """_verify_cookies_on_platform 中 page is None → False。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=None))

        result = await h_mod._verify_cookies_on_platform(mock_ctx, "xiaohongshu")
        assert result is False

    async def test_verify_returns_false_when_ctx_is_none(self, monkeypatch):
        """_verify_cookies_on_platform 中 ctx is None → False。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        result = await h_mod._verify_cookies_on_platform(None, "xiaohongshu")
        assert result is False

    async def test_extract_identity_returns_none_when_ctx_is_none(self, monkeypatch):
        """_extract_platform_identity 中 ctx is None → None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        result = await h_mod._extract_platform_identity(None, "xiaohongshu")
        assert result is None

    async def test_extract_identity_returns_none_when_no_identity_api(self, monkeypatch):
        """_extract_platform_identity 中 identity_api 为空 → None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)

        from semilabs_hone.modules.collection.scrapers.registry import get as real_get
        spec, _ = real_get("xiaohongshu")
        monkeypatch.setattr(spec.login, "identity_api", None)

        result = await h_mod._extract_platform_identity(mock_ctx, "xiaohongshu")
        assert result is None

    async def test_extract_identity_returns_none_when_page_is_none(self, monkeypatch):
        """_extract_platform_identity 中 page is None → None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=None))

        result = await h_mod._extract_platform_identity(mock_ctx, "xiaohongshu")
        assert result is None

    async def test_extract_identity_returns_none_when_body_not_dict(self, monkeypatch):
        """_extract_platform_identity 中 body 不是 dict → None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)
        mock_page = MagicMock()
        async def bad_eval(script, url):
            return "not a dict"
        mock_page.evaluate = bad_eval
        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))

        result = await h_mod._extract_platform_identity(mock_ctx, "xiaohongshu")
        assert result is None

    async def test_worker_page_exception_returns_none(self, monkeypatch):
        """_worker_page 中异常 → None。"""
        import semilabs_hone.modules.collection.handlers as h_mod

        mock_ctx = MagicMock()
        mock_ctx.pages = None  # 触发 hasattr 检查后的异常
        # Mock new_page 抛出异常
        async def bad_new_page():
            raise RuntimeError("mock error")
        mock_ctx.new_page = bad_new_page
        monkeypatch.setattr(h_mod, "_WORKER_CTX", mock_ctx)

        result = await h_mod._worker_page()
        assert result is None


class TestAccountRoutesEdgeCases:
    """补 accounts.py 路由边界覆盖率。"""

    def test_put_empty_remark_rejected(self, client, db_session):
        """PUT 空 remark → 400。"""
        aid = _seed_account(db_session, remark="orig")
        resp = client.put(f"/api/accounts/{aid}", json={"remark": ""})
        assert resp.status_code == 400

    def test_put_nonexistent_account(self, client):
        """PUT 不存在 → 404。"""
        resp = client.put("/api/accounts/999999", json={"remark": "x"})
        assert resp.status_code == 404

    def test_import_cookies_zero_account_id(self, client, monkeypatch):
        """account_id=0 → 400。"""
        _fake_ipc(monkeypatch)
        resp = client.post("/api/accounts/import-cookies",
                           data={"account_id": 0, "cookies": '[{"name":"x"}]'})
        assert resp.status_code == 400

    def test_import_cookies_not_array(self, client, db_session, monkeypatch):
        """cookie 不是数组 → 400。"""
        _fake_ipc(monkeypatch)
        aid = _seed_account(db_session, remark="arr")
        resp = client.post("/api/accounts/import-cookies",
                           data={"account_id": aid, "cookies": '{"not":"array"}'})
        assert resp.status_code == 400

    def test_import_cookies_nonexistent_account(self, client, monkeypatch):
        """导入 cookie 到不存在账号 → 404。"""
        _fake_ipc(monkeypatch)
        resp = client.post("/api/accounts/import-cookies",
                           data={"account_id": 999999, "cookies": '[{"name":"x"}]'})
        assert resp.status_code == 404

    def test_login_nonexistent_account(self, client, monkeypatch):
        """登录不存在账号 → 404。"""
        _fake_ipc(monkeypatch)
        resp = client.post("/api/accounts/999999/login")
        assert resp.status_code == 404

    def test_validate_nonexistent_account(self, client, monkeypatch):
        """验证不存在账号 → 404。"""
        _fake_ipc(monkeypatch)
        resp = client.post("/api/accounts/999999/validate")
        assert resp.status_code == 404

    def test_create_account_empty_remark_redirects_with_error(self, client):
        """remark 全空 → 重定向带 error 参数。"""
        resp = client.post("/api/accounts",
                           data={"platform": "xiaohongshu", "remark": ""},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "error=remark_required" in resp.headers.get("location", "")

    def test_ipc_client_function(self):
        """_ipc_client 返回 (IPCClient, IPCRequest)。"""
        from semilabs_hone.modules.collection.routes.accounts import _ipc_client
        cls, req_cls = _ipc_client()
        from semilabs_hone.core.ipc.client import IPCClient
        from semilabs_hone.core.ipc.protocol import IPCRequest
        assert cls is IPCClient
        assert req_cls is IPCRequest

    def test_accounts_page_with_db_error(self, client, monkeypatch, tmp_data_dir):
        """DB 查询失败时 accounts=[] 降级。"""
        from semilabs_hone.core.models.db import get_session
        from unittest.mock import MagicMock

        # Mock get_session 返回会抛异常的 session
        mock_sess = MagicMock()
        mock_sess.query.side_effect = RuntimeError("DB error")
        monkeypatch.setattr("semilabs_hone.core.models.db.get_session", lambda: mock_sess)

        resp = client.get("/accounts")
        assert resp.status_code == 200  # 降级成功


class TestQRWithWorkerCtx:
    """_do_qr_login 真 ctx 路径测试。"""

    async def test_qr_success_extract_identity(self, db_session, monkeypatch, tmp_data_dir):
        """QR 扫码成功（url 跳离登录页+匹配 success_pattern）→ 提取身份+active。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        import asyncio

        a = Account(platform="xiaohongshu", remark="qr-ok", status="inactive")
        db_session.add(a); db_session.commit()

        # Mock page：url 第一次是登录页，第二次是首页（匹配 ^/）
        class MockPage:
            def __init__(self):
                self._urls = iter([
                    "https://www.xiaohongshu.com/login",
                    "https://www.xiaohongshu.com/",
                ])
                self.context = MagicMock()
                self.context.cookies = AsyncMock(return_value=[{"name": "sid", "value": "v"}])
            @property
            def url(self):
                return next(self._urls)
            async def goto(self, url): pass
            async def wait_for_selector(self, *a, **kw): pass
            async def screenshot(self, path): pass

        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=MockPage()))
        monkeypatch.setattr(h_mod, "_WORKER_CTX", MagicMock())
        monkeypatch.setattr(h_mod, "_extract_platform_identity",
                            AsyncMock(return_value={"platform_user_id": "qr_u", "platform_nickname": "qr_nick"}))
        monkeypatch.setattr(h_mod, "_persist_cookies", lambda aid, c: None)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        # 显式恢复 timeout（防止被其他测试 monkeypatch 污染）
        from semilabs_hone.modules.collection.scrapers.registry import get as real_get
        spec, _ = real_get("xiaohongshu")
        monkeypatch.setattr(spec.login, "timeout", 120)

        msgs = []
        result = await h_mod._do_qr_login("xiaohongshu", a.id,
                                          lambda m, d=None: msgs.append((m, d)))
        assert result["login_success"] is True
        assert result["identity"]["platform_user_id"] == "qr_u"
        assert any(m == "qr_ready" for m, _ in msgs)
        assert any(m == "login_success" for m, _ in msgs)

        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account as AcctModel
        sess = get_session()
        try:
            acct = sess.query(AcctModel).get(a.id)
            assert acct.status == "active"
            assert acct.platform_user_id == "qr_u"
            assert acct.platform_nickname == "qr_nick"
        finally:
            sess.close()

    async def test_qr_timeout_fails_account(self, db_session, monkeypatch, tmp_data_dir):
        """轮询超时 → _apply_login_failure。"""
        import semilabs_hone.modules.collection.handlers as h_mod
        from semilabs_hone.core.models.account import Account
        import asyncio

        a = Account(platform="xiaohongshu", remark="timeout", status="inactive", fail_count=0)
        db_session.add(a); db_session.commit()

        # Mock page 一直停在登录页
        mock_page = MagicMock()
        mock_page.url = "https://www.xiaohongshu.com/login"
        mock_page.context = MagicMock()
        async def mock_goto(url): pass
        async def mock_wait(*a, **kw): pass
        async def mock_screenshot(path): pass
        mock_page.goto = mock_goto
        mock_page.wait_for_selector = mock_wait
        mock_page.screenshot = mock_screenshot

        monkeypatch.setattr(h_mod, "_worker_page", AsyncMock(return_value=mock_page))
        monkeypatch.setattr(h_mod, "_WORKER_CTX", MagicMock())
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        # 缩短 spec timeout（让轮询快退出），monkeypatch 自动还原
        from semilabs_hone.modules.collection.scrapers.registry import get as real_get
        spec, _ = real_get("xiaohongshu")
        monkeypatch.setattr(spec.login, "timeout", 0)

        msgs = []
        result = await h_mod._do_qr_login("xiaohongshu", a.id,
                                          lambda m, d=None: msgs.append((m, d)))
        assert result["login_success"] is False
        assert any(m == "login_timeout" for m, _ in msgs)

        # DB 验证 fail_count+1
        from semilabs_hone.core.models.db import get_session
        from semilabs_hone.core.models.account import Account as AcctModel
        sess = get_session()
        try:
            acct = sess.query(AcctModel).get(a.id)
            assert acct.fail_count == 1
            assert acct.status == "inactive"  # 1 次失败不到 5
        finally:
            sess.close()
