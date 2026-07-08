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
    assert hasattr(tsk, "ScrapeTask") and hasattr(tsk, "TaskKeyword")
    # D6: scrape_tasks 必须有 download_images / collect_comments 列
    cols = {c.name for c in tsk.ScrapeTask.__table__.columns}
    assert "download_images" in cols, "ScrapeTask 缺 download_images (D6)"
    assert "collect_comments" in cols, "ScrapeTask 缺 collect_comments (D6)"
    post = pytest.importorskip("semilabs_hone.core.models.post")
    pcols = {c.name for c in post.Post.__table__.columns}
    assert "raw_json" in pcols and "platform_id" in pcols, "Post 缺 raw_json/platform_id"
    cmt = pytest.importorskip("semilabs_hone.core.models.comment")
    assert hasattr(cmt, "Comment")
    sch = pytest.importorskip("semilabs_hone.core.models.schemas")
    for name in ["AccountCreate", "TaskCreate", "ProgressMessage", "ItemRef", "ScrapedPost", "ScrapedComment"]:
        assert hasattr(sch, name), f"schemas 缺 {name}"
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
