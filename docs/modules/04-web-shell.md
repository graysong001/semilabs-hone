# DM-04 Web 外壳（core/ui）

> 状态：✅ 已完成　|　依赖：DM-02, DM-03　|　设计依据：skim_design.md §13

## 范围
- `semilabs_hone/core/ui/app.py`
- `semilabs_hone/core/ui/ws.py`
- `semilabs_hone/core/ui/routes/dashboard.py`
- `semilabs_hone/core/ui/templates/{base,dashboard}.html`
- `semilabs_hone/core/ui/static/{style.css,app.js}`
- manifest 注册机制（各 `modules/*/manifest.py` 的契约定义）

## 目标
统一 FastAPI 应用：跨模块导航外壳、Dashboard、WebSocket 进度推送、模块路由自动注册。

## 产出接口契约

### `app.py`
```python
def create_app() -> FastAPI
# startup: init_db + setup_logger + 扫描 modules/*/manifest.py 注册 ROUTES
# 全局异常: 捕获 SkimError -> JSON {error, category, fix_hint}
# 挂 /static, Jinja2, WS 端点
```

### `ws.py`
```python
class WSManager:
    connections: set[WebSocket]
    message_buffer: deque[maxlen=50]
    async def connect(ws) -> None     # 新连回放 buffer
    async def disconnect(ws) -> None
    async def broadcast(msg: dict) -> None   # 广播 + 入 buffer
```

### manifest 契约（各模块 `manifest.py` 必须暴露）
```python
NAME: str               # UI 展示名
MODULE_ID: str          # IPC module 字段
ROUTES: list[str]       # 路由模块路径, 如 ["semilabs_hone.modules.collection.routes.accounts"]
WORKER_ENTRY: str | None  # worker 入口模块路径 (collection 有, 其他模块按需)
```
`create_app()` 启动时 `importlib` 遍历加载，把 ROUTES 的 router 挂到 app，把 NAME 加进导航。

### WS 协议
单一 dict 契约 `ProgressMessage`（DM-02 schemas），所有通知源交 `WSManager.broadcast(dict)`。`app.js` 管理 WS 连接 + 自动重连 + 按 type 分发（progress/warn/qr_ready/captcha_required/error/disk_warn/...）。

## 关键约束
- Jinja2 服务端渲染 + HTMX，**不用 React/Vue**。Pico CSS + 自定义 style.css。
- worker 不直连 WS，经 IPC client 代广播（本模块提供 broadcast 入口给 IPC client 调）。
- 同一时间每模块只 1 个 running 长任务（创建时检查，本模块提供查询接口）。

## 任务清单
- [x] `app.py`：create_app + manifest 扫描注册 + 全局异常 + static/Jinja2
- [x] `ws.py`：WSManager（connect 回放/disconnect/broadcast + buffer）
- [x] `routes/dashboard.py`：全局首页（聚合各模块概览，无账号引导卡）
- [x] `templates/base.html`：统一导航（采集/分析/制作/运营 tab）+ WS 状态指示器
- [x] `templates/dashboard.html`
- [x] `static/style.css` + `static/app.js`（WS 管理 + 进度更新 + 通知）
- [x] 定义 manifest 契约文档（写入本文件 + 各模块 manifest.py 模板）
- [x] 单测 `tests/core/test_routes.py`：TestClient 访问 / 、空库引导、SkimError→JSON

## 验收
- `python -m semilabs_hone serve` → :8530 → Dashboard 渲染、导航含"采集"tab（即便采集路由还没实现，外壳能起）。
- `pytest tests/core/test_routes.py` 绿。

## 实施记录
- 2026-07-09: 实现 create_app(FastAPI) + WSManager + 统一导航 + dashboard + manifest 注册 + WS 端点 + 全局 SkimError 处理 + Pico CSS 模板 + WS 自动重连 JS。单测 11 条。loop_gate.sh 连续两次 exit 0。collection manifest.py 更新 ROUTES/WORKER_ENTRY 契约字段。
