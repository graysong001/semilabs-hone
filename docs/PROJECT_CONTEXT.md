# PROJECT_CONTEXT — 跨会话必读背景

> **每个新会话开始时先读此文件**，再读目标模块的 `docs/modules/<NN-*.md>`，最后查 `docs/DEV_PLAN.md` 看进度。本文件是 semilabs-hone 的"项目宪法"，所有会话必须遵循。

---

## 1. 项目身份

- **项目名**：semilabs-hone（内容工厂）。
- **形态**：单体仓库，共享 `core/` + 多业务 `modules/`。首个模块 = 信息采集（`modules/collection/`，UI 展示名 "Skim"）。后续 modules/analysis（AI 分析）、production（制作）、operations（运营）预留。
- **目标平台**：**macOS 优先**。Aqua 会话拉起真 Chrome。
- **技术栈**：Python ≥3.11 / FastAPI + Uvicorn / Playwright（仅 CDP 接管）/ SQLAlchemy 2 + SQLite / Pydantic 2 / Jinja2 + HTMX + Pico CSS / loguru + tenacity / ddddocr + OpenCV / jsonpath-ng + selectolax / anthropic（Haiku）。
- **包根**：`semilabs_hone/`（下划线）。repo 根目录 `semilabs-hone/`（连字符，与 GitHub 同名）。
- **完整设计**：[docs/skim_design.md](skim_design.md)（21 节，700 行）。本文件是它的浓缩+约束提炼。

## 2. 架构一图

```
semilabs-hone web (core/ui, :8530, FastAPI+WS)  ◀──file IPC──▶  collection-browser-worker
   • 统一导航/Dashboard/SQLite/CSV                       • Aqua 拉真 Chrome + connect_over_cdp
   • IPC Client 轮询 progress/result, 代广播 WS            • 反检测/人类行为/GenericEngine/验证码
共享 SQLite (data/factory.db)                              • IPC Server 读 request 写 result
```

双进程理由：安全隔离（Chrome 持登录态）/ 崩溃恢复 / 反检测（Aqua 子进程拿真 GPU）/ 可独立重启。worker 不直连 WebSocket，进度经 `ws_events`+progress 文件由 web 代广播。

## 3. 硬约束（负面，不可妥协）

> 这些是项目宪法，任何会话写代码前必须确认不违反。

### 浏览器/反检测
- ❌ **禁止** Playwright `launch()` / `launch_persistent_context()`。只用 `subprocess.Popen` 拉系统原生 Chrome + `connect_over_cdp()`。
- ❌ **禁止** 任何自动化特征参数（`--disable-blink-features=AutomationControlled`、`--enable-automation`、`--no-sandbox` 等）。仅带 `--remote-debugging-port` + `--user-data-dir`。
- ❌ **禁止** `playwright-stealth` 完整 stealth。CDP 模式只注入 Canvas/Audio 微噪声。
- ❌ **禁止** 伪造 WebGL（`getParameter`/`getExtension`）。真 Chrome 的 WebGL/Canvas/navigator 已是真值，不覆盖 `navigator.webdriver`/`plugins`/`languages`。
- ✅ `navigator.webdriver` 必须 `undefined`。
- ✅ 一账号一**固定**指纹（viewport/color-scheme/timezone/locale），**不随机化**。UA 默认 = 本机真实 Chrome UA（CDP 读取），不伪造。

### 进程/数据
- ✅ 浏览器 worker 与 web **必须**经 file IPC（`data/ipc/{requests,results,progress,control/cancel}`）解耦，不进程内直管。
- ✅ 共享单 SQLite `data/factory.db`，跨模块靠外键互通，不复制数据。
- ✅ worker 不直连 WebSocket。

### 风控/安全
- ✅ 验证码自动解**失败 1 次即暂停**，通知人工，绝不暴力破解（账号比脚本值钱）。
- ✅ 节律：暖场 2-5 页 30-90s；笔记 30-90s；关键词 60-180s；日限 200；22:00-07:00 停跑。
- ❌ **禁止** 在代码/配置硬编码密码/密钥，用环境变量。
- ❌ **禁止** 生成 `DROP TABLE`/`TRUNCATE` 等毁灭性命令。

### 工程约定
- ✅ import 统一绝对路径：`from semilabs_hone.core.ipc.client import IPCClient`、`from semilabs_hone.modules.collection.browser.cdp import attach`。
- ✅ 原子化提交（Conventional Commits），DB/后端/前端不混提。
- ✅ 遇编译/测试/Lint 错误，最多 3 次修复，仍失败则停、报根因、等介入。

## 4. 关键裁决（已确认，不再讨论）

| 议题 | 裁决 |
| :--- | :--- |
| 多平台扩展 | **录制 step 链 + LLM(Haiku) 生成字段映射**；`platform.yaml` 为自动生成产物，正常不手写 |
| 运行时 LLM | 纯 JSONPath 为主；某条 item 校验失败仅对该条回退 LLM，**不回写映射、不做自动愈合** |
| UA 池 | 默认本机真实 Chrome UA；多样性走可配远程库+缓存，静态列表仅兜底 |
| 图片磁盘 | 30GB 报警（不中断）；硬停阈值可配默认关 |
| LaunchAgent | MVP 不启用（on-demand Popen）；P1 常驻 |
| 全厂并发 | MVP 不做（仅每模块 1 个 running cap） |
| 分析数据契约 | defer（analysis 立项再定） |
| 数据获取 | API 响应拦截优先（`page.on("response")`），DOM 仅兜底 |
| 去重 | `UNIQUE(platform, platform_id)` upsert；`raw_json` 保留；`last_note_index` 断点续传 |

## 5. 会话启动协议（每次开新会话照做）

1. 读本文件（PROJECT_CONTEXT.md）。
2. 读 `docs/DEV_PLAN.md`，选一个**状态=⬜且依赖已✅**的模块作为本次目标。
3. 读该模块 `docs/modules/<NN-*.md>`（含范围/接口契约/任务清单/验收）。
4. 必要时按需精读 `docs/skim_design.md` 对应章节（模块文档已给章节号）。
5. 开发，每完成一个任务勾掉模块文档里的 checklist。
6. 会话结束前：更新 `docs/DEV_PLAN.md` 状态表 + 该模块文档状态行；原子化提交并 push。

## 6. 会话结束协议

- 更新 DEV_PLAN.md 状态（⬜/🔄/✅）+ 模块文档顶部"状态"行。
- 在模块文档"实施记录"附一句本次做了什么/留了什么。
- `git add` 相关文件 + Conventional Commit + `git push`。

## 7. 常用路径速查

| 什么 | 在哪 |
| :--- | :--- |
| 完整设计 | docs/skim_design.md |
| 进度跟踪 | docs/DEV_PLAN.md |
| 模块 spec | docs/modules/NN-*.md |
| 全局工作流铁律 | CLAUDE.md（根，自动加载） |
| 本地偏好/环境 | CLAUDE.local.md |
| 配置 | config.py（路径/端口/节律/磁盘/UA/LLM） |
| 入口 | `python -m semilabs_hone {serve,worker,version}` |
| 运行时数据 | data/（gitignored）：factory.db / ipc/ / collection/ |
