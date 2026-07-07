# DM-11 采集-集成（collection/handlers + routes + templates）

> 状态：⬜ 未开始　|　依赖：DM-04, DM-05, DM-07, DM-09, DM-10（+ DM-08 的 platform.yaml）　|　设计依据：skim_design.md §6.3、§8.5、§9.3、§13.1、§16

## 范围
- `semilabs_hone/modules/collection/handlers.py`
- `semilabs_hone/modules/collection/routes/{accounts,tasks,posts,export}.py`
- `semilabs_hone/modules/collection/routes/templates/{accounts,task_new,task_detail,posts,post_detail}.html`
- `semilabs_hone/modules/collection/manifest.py`（补 ROUTES）

## 目标
集成层：把 browser+engine+captcha+scheduler+export 串成完整采集闭环。handlers 是 IPC op 分发到采集逻辑；routes 是采集 UI；五阶段抓取编排在此。

## 产出接口契约

### `handlers.py`（IPC op → 采集逻辑，§6.3 op 表）
```python
def build_registry() -> dict[str, Callable]
# "login":       handler_login(req) -> 三级登录 (Cookie 恢复/扫码/导入)
# "validate":    handler_validate(req) -> Cookie 是否有效
# "scrape_task": handler_scrape_task(req) -> 五阶段编排 (§9.3)
# "search/detail/comments": 单步调试
def handler_scrape_task(req: IPCRequest) -> IPCResult
# Phase1 暖场 (rhythm.check_quiet_hours+daily_limit, warmup.random_browse)
# Phase2 搜索 (engine.search, 滚动分页, keyword_delay)
# Phase3 详情 (去重 platform_id, engine.fetch_item, 按 download_images 下图, note_delay)
# Phase4 评论 (按 collect_comments, engine.fetch_comments, 前20按点赞)
# Phase5 存储 (upsert SQLite, 写 progress, 更新 last_note_index)
# 异常: CaptchaError->paused, BrowserClosed->error, QuietHours/DailyLimit->error
```
handler 内每步写 IPC progress 文件（client 代广播 WS）。

### routes（§13.1）
- `accounts.py`：GET /accounts 页 + POST /api/accounts + DELETE + POST /login + /import-cookies + /validate
  - `/login` 等 → `ipc_client.submit(module="collection", op=...)` 返回 `{request_id,status}`，前端 WS 跟踪。
- `tasks.py`：GET /tasks/new + /tasks/{id} + POST /api/tasks + /cancel + /resume
  - 平台下拉从 `registry.list_platforms()` 生成；同时只 1 running（创建时检查）。
- `posts.py`：GET /posts（筛选+分页）+ /posts/{id}
- `export.py`：GET /api/export?task_id=&keyword=&format=ai|excel

### templates
accounts/task_new/task_detail/posts/post_detail.html。task_detail 含进度条+实时日志+通知区+按钮（running=取消/failed=继续/completed=导出）。WS 驱动（core/ui app.js 已在 DM-04）。

### manifest.py
```python
NAME="Skim 采集"; MODULE_ID="collection"
ROUTES=["...accounts","...tasks","...posts","...export"]
WORKER_ENTRY="semilabs_hone.modules.collection.browser.worker_main"
```

## 关键约束
- routes 不直接调 BrowserPool（已无）——全部经 IPC client submit。
- 五阶段编排在 **worker 侧 handler**，不在 web 侧（节律以实际发起请求为准）。
- 断点续传：每篇更新 `last_note_index`；resume 从断点续，靠 `platform_id` 去重跳过。
- WS 进度经 IPC progress 文件由 web 代广播（不直连）。

## 任务清单
- [ ] `handlers.py`：build_registry + handler_login（三级）+ handler_validate
- [ ] `handlers.py`：handler_scrape_task（五阶段编排 + progress + 异常分流）
- [ ] `routes/accounts.py` + accounts.html
- [ ] `routes/tasks.py`（平台下拉 registry + 1 running 检查 + cancel/resume）+ task_new/task_detail.html
- [ ] `routes/posts.py` + posts/post_detail.html
- [ ] `routes/export.py`（调 DM-10 csv_exporter）
- [ ] manifest.py 补 ROUTES/WORKER_ENTRY
- [ ] worker_main 接入 handlers.build_registry（DM-05 留的 hook）
- [ ] 集成测试：扫码登录→新建任务→实时 progress→完成→浏览→导出（ skim_design.md §20 1-6）

## 验收（=M3 全功能里程碑）
- §20 验证方案 1-6 全通：serve→Dashboard→添加账号→扫码→新建任务→实时 progress→完成→浏览详情评论→导出 AI/Excel。
- 异常恢复（§20.7）：关 Chrome → error(BrowserClosed) → [重启浏览器] → resume 从 last_note_index 续。
- 验证码（§20.9）：滑块解失败 1 次 → paused+captcha_required → 人工 → resume。

## 依赖说明
- 需要 DM-08 产出的 `platforms/xiaohongshu/platform.yaml` 才能真跑 XHS；DM-08 未完成时可用 DM-07 占位 yaml 验收部分流程。

## 实施记录
- （待填）
