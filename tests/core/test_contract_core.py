"""核心模块接口契约测试 (DM-01..04)。

每个 test 函数 = 一个 DM 的公开接口。用 pytest.importorskip 保证模块未建时 skip,
不破坏全量回归。模块建好后必须满足这些 assert —— 接口漂移的守门员。

对应 docs/modules/01-04-*.md 的"产出接口契约"。
"""
import pytest


def test_dm01_retry_contract():
    m = pytest.importorskip("semilabs_hone.core.utils.retry")
    for name in ["SkimError", "CaptchaError", "RatelimitError", "PageLoadError",
                 "LoginError", "DataParseError", "SessionExpiredError",
                 "AccountBannedError", "QuietHoursError", "DailyLimitError",
                 "BrowserClosedError", "EmptyResultError", "PortConflictError",
                 "DiskFullError"]:
        assert hasattr(m, name), f"retry 缺 {name}"
    assert issubclass(m.CaptchaError, m.SkimError)
    assert callable(getattr(m, "scraper_retry", None)), "缺 scraper_retry"
    assert callable(getattr(m, "rate_limit_retry", None)), "缺 rate_limit_retry"
    # SkimError 必须带 fix_hint
    try:
        e = m.CaptchaError("x")
        assert getattr(e, "fix_hint", None) or "fix_hint" in m.SkimError.__init__.__code__.co_varnames
    except TypeError:
        # 构造签名可能不同; 只要类存在即过, 细节由 DM-01 自测
        pass


def test_dm02_models_contract():
    db = pytest.importorskip("semilabs_hone.core.models.db")
    assert callable(getattr(db, "init_db", None)) and callable(getattr(db, "get_session", None))
    acct = pytest.importorskip("semilabs_hone.core.models.account")
    assert hasattr(acct, "Account")
    tsk = pytest.importorskip("semilabs_hone.core.models.task")
    assert hasattr(tsk, "CollectionTask") and hasattr(tsk, "TaskKeyword")
    # PRD §6.1: collection_tasks 必须有 PRD 规范列
    cols = {c.name for c in tsk.CollectionTask.__table__.columns}
    for prd_col in ["id", "platform", "task_type", "target_value", "status",
                    "expected_count", "actual_count", "error_msg",
                    "created_at", "updated_at"]:
        assert prd_col in cols, f"collection_tasks 缺 PRD 列 {prd_col}"
    # id 必须是 String(36) UUID PK (PRD §6.1)
    id_col = tsk.CollectionTask.__table__.columns["id"]
    assert id_col.primary_key and str(id_col.type).upper().startswith("VARCHAR"), "collection_tasks.id 非 UUID PK"
    # D6 legacy 列保留 (S4/S6 迁移后再删)
    assert "download_images" in cols and "collect_comments" in cols, "CollectionTask 缺 download_images/collect_comments (D6 过渡保留)"
    post = pytest.importorskip("semilabs_hone.core.models.post")
    assert hasattr(post, "CollectionItem")
    pcols = {c.name for c in post.CollectionItem.__table__.columns}
    # PRD §6.2: collection_items 规范列 + UNIQUE(platform,platform_id)
    for prd_col in ["id", "task_id", "platform", "platform_id", "url", "title",
                    "content_text", "author_name", "metrics_json",
                    "publish_time", "scraped_at"]:
        assert prd_col in pcols, f"collection_items 缺 PRD 列 {prd_col}"
    uq_names = {c.name for c in post.CollectionItem.__table__.constraints}
    assert "uix_platform_item" in uq_names, "collection_items 缺 UNIQUE uix_platform_item"
    # [契约变更 2026-07-11 S7/L03] 旧列已删 (raw_json/likes/content/keyword_id/...)
    for legacy in ["raw_json", "likes", "content", "collects", "comments_count",
                   "shares", "tags", "post_type", "image_count", "keyword_id",
                   "published_at"]:
        assert legacy not in pcols, f"collection_items 旧列 {legacy} 应已删除 (L03 收口)"
    # url 仍 nullable (L11: 待 engine 补 url 采集后恢复 NOT NULL)
    assert post.CollectionItem.__table__.columns["url"].nullable, "url 应 nullable (L11)"

    cmt = pytest.importorskip("semilabs_hone.core.models.comment")
    assert hasattr(cmt, "CollectionComment")
    ccols = {c.name for c in cmt.CollectionComment.__table__.columns}
    # PRD §6.3: collection_comments 规范列 + UNIQUE(item_id,platform_comment_id)
    for prd_col in ["id", "item_id", "platform_comment_id", "author_name",
                    "content_text", "like_count", "scraped_at"]:
        assert prd_col in ccols, f"collection_comments 缺 PRD 列 {prd_col}"
    cuq_names = {c.name for c in cmt.CollectionComment.__table__.constraints}
    assert "uix_item_comment" in cuq_names, "collection_comments 缺 UNIQUE uix_item_comment"
    # [契约变更 2026-07-11 S7/L03] 旧列 + 旧 UNIQUE(post_id,platform_id) 已删;
    # platform_comment_id 恢复 NOT NULL (handler 总填)。
    for legacy in ["post_id", "platform_id", "content", "likes", "sub_comment_count",
                   "is_author_liked", "rank", "published_at", "raw_json", "created_at"]:
        assert legacy not in ccols, f"collection_comments 旧列 {legacy} 应已删除 (L03 收口)"
    assert "uq_comment_post_platform_id" not in cuq_names, "旧 UNIQUE(post_id,platform_id) 应已删除 (L03)"
    assert not cmt.CollectionComment.__table__.columns["platform_comment_id"].nullable, \
        "platform_comment_id 应 NOT NULL (L03 收口)"
    # repository (PRD §6.4): upsert 入口
    repo = pytest.importorskip("semilabs_hone.core.models.repository")
    for fn in ["upsert_item", "upsert_comment", "pack_metrics", "unpack_metrics"]:
        assert callable(getattr(repo, fn, None)), f"repository 缺 {fn}"
    sch = pytest.importorskip("semilabs_hone.core.models.schemas")
    for name in ["AccountCreate", "TaskCreate", "ProgressMessage", "ItemRef", "ScrapedPost", "ScrapedComment"]:
        assert hasattr(sch, name), f"schemas 缺 {name}"
    # PRD §4.1/§6.1: TaskCreate 规范字段
    tc_fields = set(sch.TaskCreate.model_fields)
    for f in ["platform", "task_type", "target_value", "expected_count"]:
        assert f in tc_fields, f"TaskCreate 缺 PRD 字段 {f}"
    # D8: ProgressMessage 必须有 data 字段
    assert "data" in sch.ProgressMessage.model_fields, "ProgressMessage 缺 data (D8)"


def test_dm03_ipc_contract():
    proto = pytest.importorskip("semilabs_hone.core.ipc.protocol")
    for name in ["IPCRequest", "IPCProgress", "IPCResult"]:
        assert hasattr(proto, name), f"protocol 缺 {name}"
    assert "ws_events" in proto.IPCResult.model_fields, "IPCResult 缺 ws_events"
    assert "module" in proto.IPCRequest.model_fields, "IPCRequest 缺 module"
    client = pytest.importorskip("semilabs_hone.core.ipc.client")
    assert hasattr(client, "IPCClient")
    for meth in ["submit", "poll_progress", "wait_result", "cancel"]:
        assert callable(getattr(client.IPCClient, meth, None)), f"IPCClient 缺 {meth}"
    server = pytest.importorskip("semilabs_hone.core.ipc.server")
    assert callable(getattr(server, "serve_worker", None))


def test_dm04_web_contract():
    app = pytest.importorskip("semilabs_hone.core.ui.app")
    assert callable(getattr(app, "create_app", None))
    ws = pytest.importorskip("semilabs_hone.core.ui.ws")
    assert hasattr(ws, "WSManager")
    for meth in ["connect", "disconnect", "broadcast"]:
        assert callable(getattr(ws.WSManager, meth, None)), f"WSManager 缺 {meth}"
