# FIX_PLAN — 设计 Review 修复计划与跟踪

> 2026-07-28 设计 review 发现 17 项问题（见本文档附录）。本文档是修复的**单一跟踪真相源**：
> 按优先级分四阶段，每项有机器可判定的验收门。完成后勾选并标注 commit。
> 总规则：原子化提交（Conventional Commits）；每步跑对应 pytest；阶段末跑 `scripts/loop_gate.sh`。

## 状态图例

⬜ 未开始　🔄 进行中　✅ 完成　⛔ 阻塞（3 次修复不过，报根因等介入）

## 阶段总览

| 阶段 | 目标 | 包含项 | 状态 |
|---|---|---|---|
| P0 | 系统能跑：打通 serve→worker→engine 真实链路 | F1–F3 | ✅ |
| P1 | 系统敢跑：安全红线真实化 | F4–F6 | ✅ |
| P2 | 符合设计：结构修补 | F7–F12 | ✅ |
| P3 | 治理：设计文档回写 + 进度校正 | F13 | ✅ |

---

## P0 系统能跑

### F1 worker 资源注入契约（review P0-1）　状态：⬜

**问题**：worker `attach()` 得到的 `ctx` 无任何通道流向 handler/engine；`_get_engine()` 建 `GenericEngine(spec=spec)` 时 `ctx=None`，真实运行时 `_ensure_page()` 必抛 RuntimeError，被 handler 吞掉后静默零抓取返回 ok。

**修复**：
- `handlers.py`：加模块级 `_WORKER_RESOURCES = {"ctx": None, "account": None}` + `set_worker_resources(ctx, account)`；`_get_engine()` 用资源构造 `GenericEngine(spec, ctx=ctx, account=account)`。
- `worker_main.py`：attach 后调 `set_worker_resources(ctx, account)`（account 从 DB 读）。
- `server.py`：`serve_worker` 增加 `account_id` 参数，请求匹配条件改为 `module 匹配 且 (req.account_id 为空 或 == 本 worker account_id)`（同时解决 review P2-8 worker 绑定矛盾）。
- `server.py`：`progress_cb` 内自检 cancel 哨兵，存在即抛 `_RequestCancelled` → 写 `cancelled` result（长任务取消即时生效，review P0-3 的 worker 侧一半）。

**验收门**：`pytest tests/core/test_ipc.py tests/collection/test_integration.py -q` 绿；新增契约测试：资源注入后 engine.ctx 非 None。

### F2 CLI 接线 + web 侧 worker 生命周期（review P0-2）　状态：⬜

**问题**：`cli.py` serve/worker 是 TODO；web 从不拉起 worker；无后台轮询，`ws_manager.broadcast` 零调用方。

**修复**：
- `cli.py`：`serve` → init_db + setup_logger + uvicorn 跑 `create_app()`；`worker --module X --account N` → 按 manifest `WORKER_ENTRY` import 并调 `main(argv)`。
- 新增 `core/ipc/supervisor.py`：`ensure_worker(module, account_id)` —— 进程内 `{(module, account_id): Popen}` 注册表，按 manifest `WORKER_ENTRY` 拉起 `sys.executable -m <entry> --account N`；死进程自动清理重启。
- 新增 `core/ui/tracker.py`：`track_request(request_id, module, task_id, account_id)` —— asyncio 后台任务：轮询 progress 文件 → 广播 WS；等 result → 广播终态/ws_events → 删 result 文件；worker 死/超时 → 广播 BrowserClosedError。
- `routes/accounts.py` / `routes/tasks.py`：每次 submit 后调 `ensure_worker` + `track_request`。

**验收门**：`python -m semilabs_hone version` 正常；`pytest tests/core/test_routes.py -q` 绿；新增测试：supervisor 注册/复用/清理、tracker 轮询广播（mock ws_manager）。

### F3 request_id 持久化 + cancel/resume 契约（review P0-3、P0-4）　状态：⬜

**问题**：cancel 用 `task-{task_id}` 与 create 的 uuid 不一致，cancel 永远无效；resume payload 缺 keywords/sort，等于空跑。

**修复**：
- `scrape_tasks` 加 `request_id VARCHAR(40)` 列；`db.py` 加 additive 列迁移助手（PRAGMA table_info 检查 + ALTER TABLE ADD COLUMN，只增不删，遵守 database.md）。
- `routes/tasks.py`：create 时存 `task.request_id`；cancel 读 `task.request_id` 发哨兵；resume 从 `task_keywords` 回读 keywords + sort_type 放进 payload，生成新 request_id 并存回。
- `handlers.py`：`handler_scrape_task` payload 缺 keywords 时从 DB 回读（task_keywords join keywords）；sort 缺省回读 task.sort_type。

**验收门**：新增测试：create→cancel 同 request_id 哨兵生效（worker 侧 cancelled）；resume payload 含原 keywords。`pytest tests/collection/test_integration.py -q` 绿。

---

## P1 系统敢跑

### F4 节律真实接线（review P1-5，安全红线）　状态：⬜

**问题**：handler 硬编码 `sleep(0.1/0.05)`，真实运行 = 零延迟狂抓 → 封号。`note_delay`/`keyword_delay` 全仓无人调用。

**修复**：`handler_scrape_task` 关键词间接 `rhythm.keyword_delay()`、笔记间接 `rhythm.note_delay()`（模块级 import 以便测试 patch）；删除全部硬编码短 sleep。测试侧 patch 为 no-op 保持快节奏。

**验收门**：新增契约测试：mock engine + patch 计数，断言关键词间调用了 keyword_delay、笔记间调用了 note_delay；`pytest tests/collection/test_integration.py -q` 绿且不慢（<10s）。

### F5 Cookie/登录真实化（review P1-6）　状态：⬜

**问题**：设计说 Chrome profile 自然持久化 Cookie，实现却读写永不存在的 `cookies.json`；`_do_qr_login` 不导航不截图返回虚构路径；profile 路径两套前缀（`profiles/<id>` vs `profiles/acct_<id>`）。

**裁决**（已确认方向）：**Chrome profile 是唯一真相**，删 cookies.json 路径；Cookie 导入 = `ctx.add_cookies()`（CDP 接管后写入真 profile）。

**修复**：
- `handlers.py` 登录三级全部改为经 ctx 真实执行：Tier1 恢复 = `ctx.cookies(base_url)` 非空 + 导航首页不被重定向到登录页；Tier2 扫码 = 导航 login_url → 截图（base64）→ progress `qr_ready` → 按 `spec.login.success_detect/success_pattern` 轮询至超时；Tier3 导入 = `ctx.add_cookies(cookies)` 后 validate。
- 删除 `_try_cookie_recovery`/`_import_cookies` 的 cookies.json 逻辑；profile 路径统一 `profiles/<id>/`（profile.py 为准）。
- `handler_validate` = 真实会话检查（同 Tier1 逻辑）。
- XHS 占位 `platform.yaml` 补全 `login` 段（login_url/success_detect/success_pattern/timeout）。

**验收门**：mock page/ctx 单测（三级路径 + 超时）；真实扫码验收留人工（🟡，记入 DM-05 人工门）。

### F6 指纹接线 + 改全局单例为按账号（review P1-7 + 新发现）　状态：⬜

**问题**：`assign_fingerprint`/`apply_fingerprint`/`get_ua` 零调用点；且 `assign_fingerprint()` 实现是**全局单例**（所有账号共享一个指纹文件），与设计"一账号一固定指纹"相悖，多账号指纹相同还有关联封号风险。

**修复**：
- `fingerprint.py`：`assign_fingerprint()` 改为每次生成新随机指纹（不再全局缓存/单例文件）；`load_fingerprint(account)` 完全从 account 字段构造（含 viewport）。
- `routes/accounts.py` 建号时：assign → 写 accounts 表四字段 + viewport_w/h + `profile_dir`（ensure_profile）。
- `worker_main.py`：attach 后从 DB 读 account → `load_fingerprint` → 资源注入时带上。
- CDP 模式落地方式（设计验证项）：timezone/locale/color-scheme 经 CDP `Emulation.*` override 逐 page 应用（`fingerprint.apply_to_page(page, fp)`）；viewport 用真实窗口（不覆盖，设计文档注明理由）。engine `_ensure_page` 创建/取得 page 后应用。

**验收门**：单测：两账号 assign 结果不同（非单例）；建号路由写入四字段；apply_to_page 调用 CDP Emulation（mock session）。

---

## P2 符合设计

### F7 ScrapedPost/Comment 补 platform（review P2-11）　状态：⬜

schemas 两模型加 `platform: str | None`；engine `_validate_group` 对 Post.body/interactions/Comments 同样注入 `spec.platform`；`_upsert_post` 用 `post.platform or payload platform`（不再硬编码 xiaohongshu）。
**验收门**：`pytest tests/collection/test_engine.py tests/collection/test_integration.py -q` 绿。

### F8 模板归位 + 多目录加载（review P2-12）　状态：⬜

collection 5 个业务模板（accounts/task_new/task_detail/posts/post_detail）从 `core/ui/templates/` 移到 `modules/collection/routes/templates/`；`app.py` 用 Jinja2 ChoiceLoader（core 模板目录 + 各模块 routes/templates 目录，Starlette 支持 directory 传 list）；core 只留 base/dashboard。
**验收门**：`pytest tests/core/test_routes.py tests/collection/test_integration.py -q` 绿（测试 helper 同步多目录）。

### F9 captcha paused + ws_events 通路（review P2-10）　状态：⬜

`server.py`：异常路径若 `category=="captcha"` → status `paused` + `ws_events:[captcha_required]`；handler 返回 dict 带 `ws_events` 键时透传进 result。`handler_scrape_task` 捕获 CaptchaError 时先把 task 状态置 paused 再抛出。
**验收门**：测试：CaptchaError → IPCResult.status=="paused" 且 ws_events 非空。

### F10 engine DOM 兜底（review P2-9）　状态：⬜

extract step：`saved` 响应为空时调 `field_extract.extract_dom(page, group, map)` 兜底（XHR 拦截优先、DOM 兜底的 Layer 5 补全）。
**验收门**：测试：wait_xhr 超时返回空 → extract 走 DOM 路径产出 item。

### F11 IPC gc + 断点跳过（review P2-13、P2-14）　状态：⬜

`server.py`：写 result 后删 request 文件；每 60 轮 poll gc 一次超 1h 的 results/progress/cancel 孤儿；`serve_worker` 加 `idle_timeout`（默认 `config.WORKER_IDLE_TIMEOUT`），超时退出主循环，worker_main 退出前 `proc.terminate()` Chrome。`client.wait_result` 读到 result 后删文件。`handler_scrape_task` resume 时从 posts 表读该 task 已抓 platform_id 预填 `seen_ids`（跳过详情/评论重复请求）。
**验收门**：`pytest tests/core/test_ipc.py -q` 绿 + 新增 gc/idle/cleanup 测试；resume 跳过测试。

### F12 engine LLM 模型走 config（review P2-15）　状态：✅ (9d1c6a8)

`_llm_fallback` 用 `config.LLM_MODEL`（lazy import），删硬编码 `claude-haiku-4-5-20250414`。
**验收门**：测试断言调用模型名 == config.LLM_MODEL。

---

## P3 治理

### F13 设计文档回写 + 进度校正（review P3-16/17）　状态：✅

- `skim_design.md` 补 §6.6「worker 资源注入与生命周期契约」（F1/F2 的裁决固化：资源注入方式、per-(module,account) 匹配、supervisor/tracker 职责）。
- `skim_design.md` §4.3 注明：CDP attach 模式下 viewport 用真实窗口，timezone/locale/color-scheme 经 Emulation override。
- `DEV_PLAN.md`：DM-03（gc 未做）、DM-05（CLI 未接线）状态行补注；加本文档链接。
- 建议新增冒烟门（人工）：真 Chrome 跑一次 search flow 返回非空 ItemRef，记入 DM-11 人工门。

**验收门**：文档提交；`scripts/loop_gate.sh` 退出 0。

---

## 附录：review 问题清单（2026-07-28）

| # | 级别 | 问题 | 对应修复 |
|---|---|---|---|
| 1 | P0 | ctx 无通道流向 handler/engine，真实链路必断 | F1 |
| 2 | P0 | web 不拉 worker、无后台轮询、cli 是 TODO | F2 |
| 3 | P0 | cancel 的 request_id 不一致，永远无效 | F3 |
| 4 | P0 | resume 丢 keywords/sort，空跑 | F3 |
| 5 | P1 | 节律被硬编码 sleep 短路（封号风险） | F4 |
| 6 | P1 | Cookie 两套矛盾、QR 登录是假的、profile 路径不一致 | F5 |
| 7 | P1 | 指纹/UA 死代码；assign 是全局单例 | F6 |
| 8 | P2 | worker per-account vs 队列 per-module 矛盾 | F1 |
| 9 | P2 | DOM 兜底名存实亡 | F10 |
| 10 | P2 | captcha 应 paused+ws_events，实际 error 且无 ws_events | F9 |
| 11 | P2 | ScrapedPost/Comment 缺 platform，多平台串号 | F7 |
| 12 | P2 | collection 模板污染 core/ui/templates | F8 |
| 13 | P2 | IPC 文件无清理、无 gc、无 idle 退出 | F11 |
| 14 | P2 | last_note_index 断点语义未兑现（重抓全部） | F11 |
| 15 | P2 | engine LLM 模型名与 config 不一致 | F12 |
| 16 | P3 | DoD 全 mock 绿，真实链路从未验证 | F13 |
| 17 | P3 | 模块状态与 checklist 漂移 | F13 |
