# semilabs-hone — 详细设计文档 (DESIGN.md)

> 项目名 **semilabs-hone**（内容工厂）。本文档对 `skim.spec` 做架构级 review，并把整套设计定位为**多平台可扩展的内容工厂单体仓库**：信息采集（`modules/collection/`，UI 展示名 "Skim"）是第一个模块，后续扩展 `modules/analysis/`（AI 分析）、`modules/production/`（制作）、`modules/operations/`（运营）。本文档只到设计，不含实现代码。

> **实施已切分为 12 个独立开发模块**，便于单会话按模块细化开发、控制上下文。跨会话先读 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)，查 [DEV_PLAN.md](DEV_PLAN.md) 选模块，读 `modules/NN-*.md` 看 spec。

---

## Context（背景与动机）

`skim.spec` 是从既有 Linux 系统（`/home/admin/workspace/skim/`）搬运的规格书，大量"复用" `cargo-tracker`，采用 **FastAPI 进程内 BrowserPool**。但当前 macOS 机器上：项目是空白画布（只有 `skim.spec`）、`cargo-tracker` 不在本机、spec 写 Linux 路径、角色约束要求 **macOS LaunchAgent + Aqua + 真实 GPU**、且**强制 file IPC 进程解耦**；同时 spec 把采集当成单产品、只支持小红书、不支持快速加新站点。

本文档使命：**对 spec 做架构级 review，裁决 spec 与角色约束的分歧，升级为多平台可扩展的内容工厂单体仓库**。所有"复用 cargo-tracker"的模块改为**从零定义接口与伪代码**，不引用外部项目。

### 关键裁决（已与用户确认）

| 议题 | 裁决 |
| :--- | :--- |
| 目标平台 | **macOS 优先**。Aqua 会话拉起 Chrome，真实 WindowServer+GPU。 |
| 进程架构 | **file IPC 双进程**。浏览器作为独立 worker，`request→result+progress` 文件队列与 Web 解耦。 |
| cargo-tracker | **自包含，不依赖**。 |
| 模块耦合 | **共享 core + 模块包**。`core/` 共享 SQLite/FastAPI 外壳/config/utils/IPC 总线；各模块写自己的业务与路由。 |
| 采集模块命名 / IPC | 包名 `modules/collection/`（UI 展示名 Skim）；file IPC 升级为 `core/ipc/` 全厂任务总线。 |
| **多平台扩展** | **录制 step 链 + LLM(Haiku) 生成字段映射**。加站 = 在 Chrome 点一遍 + 指派 XHR→schema group，`platform.yaml` 为自动生成产物，不手写（§8、§19）。 |
| **运行时 LLM** | 纯 JSONPath 为主；某条 item 校验失败时**仅对该条**回退 LLM 兜底，**不回写映射、不做自动愈合**（§8.4）。 |
| **UA 池** | **不依赖静态列表**：默认用本机真实 Chrome UA（CDP 读取，零伪造）；需多样性时实时抓取远程 UA 库 + 缓存，静态列表仅兜底（§5.3）。 |
| **图片磁盘** | **30GB 报警**（WS warn + UI 角标）；硬停阈值可配、默认关。 |
| LaunchAgent / 跨模块并发 / 分析契约 | 按 MVP 必要：LaunchAgent **MVP 不启用**（on-demand Popen）；全厂并发 **MVP 不做**（仅每模块 1 个 running cap）；分析数据契约 **defer**。 |

---

## 0. Review 结论：spec 与角色约束的分歧清单

| # | 分歧点 | spec 现状 | 裁决 |
| :--- | :--- | :--- | :--- |
| D1 | 平台 | Linux 路径 | 改 macOS + LaunchAgent plist 设计（§4） |
| D2 | 进程解耦 | 进程内 BrowserPool | 重构为 file IPC 双进程（§1、§6） |
| D3 | stealth | requirements 列 `playwright-stealth` | 移除完整 stealth，仅 Canvas/Audio 噪声，**不伪造 WebGL**（§5.2） |
| D4 | 参考实现 | 引用 cargo-tracker 行号 | 全部自包含（全文） |
| D5 | 浏览器启动 | 未禁止 automation flag | 负面硬约束：禁止任何自动化特征参数（§4.1） |
| D6 | task 字段缺失 | TaskCreate 有 download_images/collect_comments，表无列 | scrape_tasks 补两列（§7.1） |
| D7 | WS 契约不一致 | _emit 位置参数 / broadcast(dict) / callback 混用 | 统一 dict 契约交 WSManager.broadcast（§13.3） |
| D8 | ProgressMessage 缺 data | schemas.py 缺 data | 补 `data: dict | None`（§13.3） |
| D9 | browser_pool 接口不匹配 | 缺 get_page/release_page | IPC 架构下概念消失（§6.5） |
| D10 | feed API 方法 | 表写 GET，正文写 POST | 统一 POST（§9.1） |
| D11 | Section 20.2 截断 | 文末截断 | 重新完整给出已验证清单（§9.1） |
| D12 | 单产品定位 | skim 当整个产品 | 升级为内容工厂单体仓库，skim=modules/collection/（§3） |
| D13 | 单平台耦合 | spec 把 XHS 写死在各模块 | 抽象 BasePlatformScraper + 录制+LLM 生成映射，XHS 变为一个 platform 实例（§8） |

---

## 1. 总体架构

一个单体仓库 + 一个 Web 外壳 + 多个 worker 进程：

- **semilabs-hone web**（`core/ui`）：统一 FastAPI 应用（:8530），跑在 Aqua 会话，提供跨模块导航外壳、Dashboard、WebSocket 进度推送。各模块把路由注册进外壳。
- **core/ipc 任务总线**：全厂跨模块/跨进程文件队列。任何耗时任务（采集浏览器 worker、未来分析/制作 AI 任务）都作为独立 worker，通过 `request→result+progress` 与 web 解耦。
- **共享 SQLite**（`data/factory.db`）：所有模块读写同一库，跨模块靠外键互通。

### 1.1 当前（采集模块）进程拓扑

```
┌──────────────────────────────┐       ┌──────────────────────────────────┐
│  semilabs-hone web (FastAPI)  │       │  collection-browser-worker        │
│  core/ui ( :8530 )            │       │  modules/collection/browser       │
│  ─────────────────────────── │       │  ──────────────────────────────  │
│  • 统一导航: 采集/分析/制作/运营 │       │  • Aqua 会话拉起原生 Chrome        │
│  • REST API + WebSocket       │  IPC  │    (--remote-debugging-port,      │
│  • 任务编排 / 节律调度         │ 总线  │     无 automation flag)           │
│  • 共享 SQLite 读写            │ ◀──▶ │  • connect_over_cdp 接管          │
│  • IPC Client: 写 request,    │ 文件  │  • 反检测 / 人类行为 / 抓取        │
│    轮询 result/progress        │ 队列  │  • GenericEngine 驱动 platform.yaml│
│  • WSManager 广播进度          │       │  • 验证码检测 + 暂停               │
└──────────────────────────────┘       │  • IPC Server: 读 request, 写     │
        ▲                              │    result/progress                │
        │ WebSocket                    └──────────────────────────────────┘
        ▼                                      ▲ connect_over_cdp
   浏览器 localhost                            │
                                        ┌────┴────┐
                                        │ 真实    │ ← 一账号一 profile 目录
                                        │ Chrome  │   (--user-data-dir)
                                        └─────────┘
```

> 未来 `modules/analysis/`、`modules/production/` 各起 worker 挂同一 IPC 总线，web 统一调度。浏览器 worker 是 collection 模块**独有**的（只有采集需要真 Chrome）。

### 1.2 为什么双进程（采集侧）

安全隔离（Chrome 持登录态，Web 崩不丢会话）/ 崩溃恢复（Chrome 死 → worker 写 BrowserClosedError result）/ 反检测（Aqua 子进程拿真实 GPU 上下文，进程内 launch() 拿不到）/ 可独立重启。

### 1.3 生命周期

- **web**：`python -m semilabs_hone serve` 启动，常驻 Aqua 会话。
- **collection-browser-worker**：web 在需要时 `subprocess.Popen` 拉起；空闲超时（默认 10 min）自动退出，按需重启。

---

## 2. 分层职责（core vs modules）

| 层 | 归属 | 职责 |
| :--- | :--- | :--- |
| 配置/日志/工具 | `core/` | config、logger、retry（异常基类）——全厂复用 |
| 数据库/模型 | `core/models/` | 共享 engine+Session+全部表（采集表+未来表） |
| IPC 总线 | `core/ipc/` | request/result/progress schema、client、server、paths——全厂跨进程 |
| UI 外壳 | `core/ui/` | FastAPI 工厂、WSManager、base 模板、统一导航、static |
| 模块路由 | `modules/<m>/routes/` | 各模块页面与 API，注册进外壳 |
| 模块业务 | `modules/<m>/` | 采集=浏览器/反检测/通用引擎/平台配置/验证码/调度；分析/制作/运营各自 |
| 模块 worker | `modules/<m>/worker_main.py` | 该模块独立 worker 入口 |
| 运行时数据 | `data/` | 共享 DB + 全厂 IPC + 各模块子目录 |

**import 约定**：包根 `semilabs_hone`，统一绝对路径。例：`from semilabs_hone.core.ipc.client import IPCClient`、`from semilabs_hone.modules.collection.scrapers.engine import GenericEngine`、`from semilabs_hone.core.models.post import Post`。

---

## 3. 目录结构

```text
内容工厂/                            # 仓库根 (工作目录)
├── pyproject.toml                  # name="semilabs-hone", 包=semilabs_hone
├── main.py                         # 入口: serve / worker --module collection / version
├── config.py                       # 全局配置 (路径、端口、限额、安静时段、磁盘阈值)
├── requirements.txt
├── README.md  DESIGN.md
│
├── semilabs_hone/                  # ── Python 包根 ──
│   ├── core/                       # 跨模块共享
│   │   ├── config.py
│   │   ├── ipc/{protocol,client,server,paths}.py
│   │   ├── models/{db,account,keyword,task,post,comment,schemas}.py
│   │   ├── ui/{app,ws}.py  routes/dashboard.py  templates/{base,dashboard}.html  static/{style.css,app.js}
│   │   └── utils/{logger,retry,image_downloader}.py
│   │
│   ├── modules/
│   │   ├── collection/             # 信息采集 (= Skim)
│   │   │   ├── manifest.py         # 模块元信息 + 路由注册表 + worker 入口
│   │   │   ├── browser/{cdp,launchagent,profile,worker_main}.py
│   │   │   ├── anti_detect/{stealth,human_behavior,fingerprint,ua_pool}.py
│   │   │   ├── scrapers/
│   │   │   │   ├── base.py         # BasePlatformScraper ABC + ItemRef/Item/Comment + schema groups
│   │   │   │   ├── registry.py     # 启动扫描 platforms/*/platform.yaml 自动注册
│   │   │   │   ├── spec.py         # PlatformSpec pydantic (flow/step/group schema)
│   │   │   │   ├── recorder.py     # CDP 录制器: 捕获 step 链 + 数据 XHR 样本
│   │   │   │   ├── llm_mapper.py   # JSON 样本 + schema group -> JSONPath (Haiku)
│   │   │   │   ├── engine.py       # 运行时引擎: 回放 step 链 + JSONPath 提取 + 失败兜底
│   │   │   │   ├── field_extract.py# JSONPath + CSS 取值
│   │   │   │   └── platforms/
│   │   │   │       ├── xiaohongshu/{platform.yaml(生成), adapter.py(可缺省)}
│   │   │   │       ├── zhihu/      (预留)
│   │   │   │       └── ...
│   │   │   ├── captcha/{solver,slide_solver,ocr_solver,manual_handler}.py
│   │   │   ├── scheduler/{rhythm,warmup}.py
│   │   │   ├── export/csv_exporter.py
│   │   │   ├── handlers.py         # IPC op -> 采集逻辑 (login/search/detail/scrape_task)
│   │   │   └── routes/{accounts,tasks,posts,export}.py + templates/
│   │   ├── analysis/               # P1 预留
│   │   ├── production/             # P2 预留
│   │   └── operations/             # P3 预留
│   │
│   └── (包 __init__)
│
├── data/                           # 运行时 (gitignored)
│   ├── factory.db                  # 共享 SQLite
│   ├── logs/
│   ├── collection/{images,profiles,exports,debug,ua_pool.json}/
│   ├── ipc/{requests,results,progress,control/cancel}/
│   └── analysis/  production/  operations/   (未来)
│
└── tests/{core,collection}/
```

> 与 spec 差异：① 包根 `semilabs_hone`；② `core/` 共享层；③ 采集下沉 `modules/collection/`；④ **`scrapers/` 重构为 `engine + spec + platforms/<name>/platform.yaml`**，支持声明化加站（D13）；⑤ `anti_detect/ua_pool.py`（D-UA）；⑥ 删除进程内 `browser/pool.py`。

---

## 4. 浏览器进程设计（macOS + CDP）

> 属 `modules/collection/browser/`，采集模块独有。

### 4.1 干净 Chrome + CDP 接管（Layer 1）

**硬约束（负面，不可妥协）：**

- ✅ `subprocess.Popen` 拉起系统原生 Chrome（`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`）；
- ✅ 仅带 `--remote-debugging-port=<port>`、`--user-data-dir=<profile_dir>`；
- ❌ 禁止 `--disable-blink-features=AutomationControlled` / `--enable-automation` / `--no-sandbox` 等任何自动化特征参数；
- ❌ 禁止 Playwright `launch()` / `launch_persistent_context()`；
- ✅ `connect_over_cdp(f"http://127.0.0.1:{port}")` 接管。

**效果**：`navigator.webdriver === undefined`，无 CDP 痕迹，过 RS WAF 真实 GPU 渲染检测。

伪代码（`modules/collection/browser/cdp.py`）：

```python
def launch_real_chrome(profile_dir: str, port: int) -> subprocess.Popen:
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    args = [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}"]
    return subprocess.Popen(args, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)

async def attach(port: int) -> tuple[Browser, BrowserContext]:
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    return browser, ctx
```

**端口冲突**：探测 9333-9340，被占递增；区分"自己旧 worker 占"（复用）vs"别的程序占"（换端口）。

### 4.2 macOS LaunchAgent（Aqua 会话）

`browser/launchagent.py` 生成 plist：Label `com.semilabs.collection-worker`、`LimitLoadToSessionType=Aqua`、Stdout/Stderr → `data/logs/collection-worker.log`。**MVP 不启用**（on-demand Popen 即可，web 本在 Aqua，子进程继承 GUI）；P1 常驻增强，给出 plist 模板。

### 4.3 一账号一固定指纹 Profile（Layer 2）

- 账号创建时**一次性固定**：viewport、color-scheme、timezone、locale，写入 `accounts` 表，**不随机化**；
- UA 默认 = 本机真实 Chrome UA（见 §5.3），不覆盖；
- `--user-data-dir=data/collection/profiles/<account_id>/` 隔离 → Cookie/localStorage 自然持久化。

### 4.4 最小化 Stealth 注入（Layer 3）

移除 `playwright-stealth`。真 Chrome 的 navigator/WebGL/Canvas 已是真值，**伪造反而暴露**。只注入 `NOISE_ONLY_SCRIPT`：

- ✅ Canvas `toDataURL`/`getImageData` 像素级微噪声；✅ AudioContext `getChannelData` 微噪声；
- ❌ 不伪造 WebGL `getParameter`/`getExtension`；❌ 不覆盖 `navigator.webdriver`/`plugins`/`languages`。
- 注入：`ctx.add_init_script`，每次导航前，仅噪声脚本。

---

## 5. 反检测六层（采集模块）

| Layer | 模块 | 要点 |
| :--- | :--- | :--- |
| 1 干净 Chrome + CDP | `browser/cdp.py` | 真实 Chrome + connect_over_cdp，无 flag（§4.1） |
| 2 一账号一固定指纹 | `anti_detect/fingerprint.py` `browser/profile.py` | viewport/color-scheme/timezone 一次性固定不随机化（§4.3） |
| 3 最小噪声注入 | `anti_detect/stealth.py` | 仅 Canvas/Audio，不碰 WebGL（§4.4） |
| 4 人类行为 | `anti_detect/human_behavior.py` | 逐字符 50-200ms + 5% 长停顿；贝塞尔鼠标 + 随机偏移；滑块先加速后减速 + 过冲回弹；random_scroll/browse 暖场 |
| 5 API 拦截优先 + DOM 兜底 | `scrapers/engine.py` | on("response") + Future + wait_for 超时兜底 DOM（§8.5） |
| 6 节律调度 | `scheduler/rhythm.py` `warmup.py` | 暖场 2-5 页 30-90s；笔记 30-90s；关键词 60-180s；日限 200；22:00-07:00 停跑；验证码失败 1 次即暂停（§12） |

### 5.1 UA 池设计（`anti_detect/ua_pool.py`）—— 不依赖静态列表

**默认策略（最重要）**：UA = **本机真实 Chrome 的 UA**。worker `attach()` 后通过 `page.evaluate("navigator.userAgent")` 读出真实 UA，直接作为该机器所有账号的 UA，**不覆盖、不伪造**。理由：UA 与真实浏览器版本/平台 100% 一致，零不匹配，最不可检测；一台机器多账号共享同一 UA 本就是正常用户行为。

**多样性策略（默认关闭，可配开启）**：当确实需要跨账号 UA 多样性时，从**可配置的远程 UA 库**实时抓取（HTTP GET 一个用户自填的 UA 列表端点或公开 UA DB），过滤出与本机 Chrome 主版本号一致的 macOS Chrome UA，缓存到 `data/collection/ua_pool.json`（TTL 24h），分配时从缓存取。**bundled 静态列表仅作离线兜底**，且打 `stale` 标记告警。

```python
async def get_ua(ctx, account) -> str:
    if config.UA_STRATEGY == "real":          # 默认
        page = await ctx.new_page()
        return await page.evaluate("navigator.userAgent")
    # variety: 从远程库抓取 + 缓存, 过滤匹配本机 major version
    return await _pick_from_live_pool(ctx)
```

> 决策依据：用户明确"不要仅依赖网上的静态列表"。真实 Chrome UA 是 ground truth；远程库只是补充多样性，且必须与本机版本对齐避免不匹配。

### 5.2 API 拦截 + DOM 兜底（Layer 5，伪代码，GenericEngine 通用）

```python
async def fetch_via_api(page, url, matcher, parser, dom_fallback):
    fut = asyncio.get_running_loop().create_future()
    def _cap(resp):
        if matcher(resp) and not fut.done(): fut.set_result(resp)
    page.on("response", _cap)
    try:
        await page.goto(url)
        data = await asyncio.wait_for(asyncio.shield(fut), timeout=15)
        return await parser(data)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return await dom_fallback(page)
    finally:
        page.remove_listener("response", _cap)
```

---

## 6. IPC 任务总线（`core/ipc/`，全厂共享）

### 6.1 文件队列

根 `data/ipc/`；`requests/<id>.json`、`results/<id>.json`、`progress/<id>.json`（流式覆盖）、`control/cancel/<id>` 哨兵；原子写（`.tmp`→`os.rename`）；result 取走后删，定期 gc 超 1h 孤儿。

### 6.2 通用 schema

```python
class IPCRequest(BaseModel):
    request_id: str; module: str; op: str
    account_id: int | None = None; payload: dict; created_at: float
class IPCProgress(BaseModel):
    request_id: str; message: str; data: dict | None; updated_at: float
class IPCResult(BaseModel):
    request_id: str; status: Literal["ok","error","paused","cancelled"]
    data: dict | None = None; error: dict | None = None
    ws_events: list[dict] | None = None; completed_at: float
```

> worker 不直连 WS：进度经 `ws_events`（终态）+ `progress` 文件（流式）由 client 代广播。任何模块 worker 复用同一推送机制。

### 6.3 op 路由

`module+op` 决定分发。采集 op（`modules/collection/handlers.py`）：

| op | payload | result.data |
| :--- | :--- | :--- |
| `login` | `{platform}` | `{status, qr_screenshot?}` |
| `validate` | `{platform}` | `{valid}` |
| `scrape_task` | `{task_id, platform, keywords[], sort, max_posts, download_images, collect_comments}` | `{posts_scraped, comments_count, images_count, last_note_index}` |
| `search/detail/comments` | `{platform, ...}` | 单步调试 |
| `cancel` / `shutdown` | — | — |

> 采集 op 全部带 `platform` 字段，由 GenericEngine 路由到对应 platform.yaml。未来分析 op（analyze_topic/embed_posts/summarize）同样走总线，`module="analysis"`。

### 6.4 client / server

**Client**（web 侧）：`submit` / `poll_progress` / `wait_result` / `cancel`。
**Server**（`core/ipc/server.py`，通用主循环）：轮询 `requests/` 取最早且 `module` 匹配自己的 → 查 handler 表分发 → 流式写 progress → 自检 cancel → 写 result。各 `worker_main.py` 只需启动资源（采集=拉 Chrome+attach+注入噪声）+ 注册 handler + 跑主循环。

### 6.5 D9 修复

BrowserPool 概念消失：worker 全程持有 Chrome+ctx，不向 Web 暴露 page；scraper 在 worker 内直接用 `attach()` 的 ctx。

---

## 7. 数据模型（共享 SQLite）

全部表在 `core/models/`，读写同一 `data/factory.db`。沿用 spec §4 采集表（已天然 platform-agnostic：`posts.platform`、`accounts.platform`、`UNIQUE(platform, platform_id)`），做修订：

### 7.1 `scrape_tasks` 补字段（D6）

```sql
download_images BOOLEAN DEFAULT TRUE,
collect_comments BOOLEAN DEFAULT TRUE,
```
handler 必读。另：`scrape_tasks.platform` 已有，复用。

### 7.2 `accounts` 补指纹字段

```sql
color_scheme VARCHAR(10) DEFAULT 'light',
timezone VARCHAR(40) DEFAULT 'Asia/Shanghai',
locale VARCHAR(20) DEFAULT 'zh-CN',
```
（UA 不入库，运行时从真实 Chrome 读取，见 §5.3。）

### 7.3 其余采集表

`keywords`/`task_keywords`/`posts`/`comments` 沿用 spec，不改。关键决策：`platform_id` 去重 upsert、`raw_json` 保留（为 AI 分析预留）、`last_note_index` 断点续传。

### 7.4 跨模块数据互通（预留）

分析模块直接读 `posts`/`comments`（不复制），新增 `post_embeddings`/`topic_summaries` 等表经 `post_id` 外键关联。新表在 `core/models/analysis.py`，随模块开发加入 `init_db()`。

---

## 8. 多平台适配架构（录制 + LLM 生成映射）

> 回答"如何快速加站 + 如何指定抓取内容（多步跳转 + 内容页多块选取）"。核心：**用户在真 Chrome 里点一遍，recorder 录制步骤链 + 捕获数据 XHR 样本，LLM(Haiku) 把样本映射到统一 schema，`platform.yaml` 是自动生成的产物，正常流程不手写。** 因为用 API 响应拦截（不自构请求），平台差异塌缩为"步骤链 + XHR 匹配 + 字段映射"，全部可由录制+LLM 产出。

### 8.1 三个核心概念

- **Flow（采集流）**：一个完整动作链，对应一个数据目标。每平台三条标准 flow：`search`（搜索列表）、`detail`（详情）、`comments`（评论）。每条 flow = 一条**步骤链**。
- **Step（步骤）**：链中一步，类型：`navigate`（去 URL）/`input`（输入）/`click`（点元素）/`scroll`（滚动触发分页）/`wait_xhr`（等数据 XHR 并存响应）/`extract`（从存的响应抽字段）。**多步跳转 = 一串 step，由录制捕获，不手写。**
- **Schema Group（内容块）**：统一 schema 的字段分组——`Post.body`（title/content/author/image_urls/tags/published_at）、`Post.interactions`（likes/collects/comments_count/shares）、`Comments`（author/content/likes/rank）。**用户不逐字段选，只把"哪条 XHR 响应对应哪个 group"指派一次**，group 内字段由 LLM 自动映射。

模块文件：
- `scrapers/spec.py`：`PlatformSpec` pydantic（flow/step/group schema，即 platform.yaml 的模型）；
- `scrapers/recorder.py`：CDP 录制器，捕获 step 链 + 数据 XHR 样本；
- `scrapers/llm_mapper.py`：JSON 样本 + group → JSONPath（Haiku 结构化输出）；
- `scrapers/engine.py`：运行时引擎，回放 step 链 + JSONPath 提取 + 失败兜底；
- `scrapers/base.py`：`BasePlatformScraper` ABC + `ItemRef`/`Item`/`Comment` + schema group 定义；
- `scrapers/registry.py`：启动扫描 `platforms/*/platform.yaml` 自动注册；
- `scrapers/field_extract.py`：JSONPath + CSS 取值。

### 8.2 platform.yaml（运行时格式，由录制+LLM 生成）

录制+LLM 完成后系统写回此文件，可读、可手改微调、可版本管理。结构以 `flows` 为中心：

```yaml
platform: example_site
display_name: Example
base_url: https://www.example-site.com
login: {type: qrcode, login_url: /login, success_detect: url_change, success_pattern: "^/$", timeout: 120}

flows:
  search:                       # 一条 flow = 一条 step 链 (多步跳转由此表达)
    steps:
      - {type: navigate, url: "/search?q={keyword}&sort={sort}"}
      - {type: input, locator: {text: "搜索"}, text: "{keyword}"}
      - {type: scroll, max_times: 5, wait_ms: 800}
      - {type: wait_xhr, url_pattern: "/api/search", method: GET, save_as: "search_resp"}
      - {type: extract, from: "search_resp", group: "ItemRef",
         map: {item_id: "$.id", title: "$.title", author_name: "$.user.name", likes: "$.stats.likes"}}
  detail:
    steps:
      - {type: navigate, url: "/p/{item_id}"}
      - {type: wait_xhr, url_pattern: "/api/feed", save_as: "feed_resp"}
      - {type: extract, from: "feed_resp", group: "Post.body",                 # 内容块指派: feed_resp -> 正文+互动
         map: {title: "$.title", content: "$.desc", author_name: "$.author.name", image_urls: "$.images[*].url", published_at: "$.time"}}
      - {type: extract, from: "feed_resp", group: "Post.interactions",
         map: {likes: "$.stats.likes", collects: "$.stats.collects", comments_count: "$.stats.comments", shares: "$.stats.shares"}}
  comments:
    steps:
      - {type: scroll, max_times: 3, wait_ms: 800}
      - {type: wait_xhr, url_pattern: "/api/comments", save_as: "cmt_resp"}    # 内容块指派: cmt_resp -> 评论
      - {type: extract, from: "cmt_resp", group: "Comments",
         map: {platform_id: "$.id", author_name: "$.user.name", content: "$.text", likes: "$.likes"}}

sort_values: {general: general, time_descending: latest, popularity_descending: hot}
```

> **如何指定抓取内容**：① 多步跳转 = `steps` 链（录制生成，不手写）；② 内容页多块 = 把 `wait_xhr` 保存的响应指派给 `group`（`feed_resp→Post`、`cmt_resp→Comments`），group 内字段由 LLM 自动映射。要加 schema 之外的字段填一句自然语言描述，LLM 定位生成 JSONPath。

**字段表达式语法**（`field_extract.py`）：API 用 JSONPath（`$.data.items[*].note_id`）；DOM 兜底用 `css:<sel>` 取 text、`css:<sel>@<attr>` 取属性、`xpath:<expr>` 取节点。

### 8.3 录制器（`recorder.py`，CDP 驱动）

用户点"添加站点 → 录制"→ worker 开 Chrome → 用户在真 Chrome 操作：
- 每个导航/点击/输入/滚动，recorder 经 CDP 捕获为 step；点击元素捕获**多策略** selector（text/role/aria-label/nth-of-type），运行时按优先级回退降脆弱；
- 每个网络响应记录 url/method/JSON 样本；
- 录制结束把"操作时序 + 响应"对齐，**启发式标注**哪步触发了哪个数据 XHR（操作后短时间内到达的大体积 JSON）；
- 产物：每条 flow 的 step 链 + 每个 `wait_xhr` 的 JSON 样本。UI 步骤编辑器可删闲步、把关键词参数标成 `{keyword}` 占位符。

### 8.4 LLM 映射器（`llm_mapper.py`，Haiku 档）

输入：某 `wait_xhr` 的 JSON 样本 + 目标 schema group（字段列表 + 自然语言说明）。输出：group 各字段 JSONPath（结构化输出强制）。
- 例：feed 样本 + `Post.body+interactions` → `{title: "$.data.note.title", content: "$.data.note.desc", likes: "$.data.note.interact_info.liked_count", ...}`；
- **自定义字段**：用户填一句自然语言描述（如"视频时长"）→ LLM 在样本定位 → 生成 JSONPath，加进 group 的 `custom_fields`；
- 映射过**样本校验**：用样本 JSON 跑一遍 JSONPath，字段非空才通过；失败让 LLM 重试或标红人工确认。

### 8.5 运行时引擎（`engine.py`）

运行时**纯 JSONPath，不调 LLM**（快、免费、确定）：

```python
class GenericEngine(BasePlatformScraper):
    def __init__(self, spec, ctx, account): ...
    async def run_flow(self, flow_name, **vars) -> list:
        saved = {}; out = []
        for step in self.spec.flows[flow_name].steps:
            match step.type:
                case "navigate": await human_goto(self.page, render(step.url, **vars))
                case "input":    await human_type(self.page, step.locator, render(step.text, **vars))
                case "click":    await human_click(self.page, step.locator)
                case "scroll":   await random_scroll(self.page, step.max_times, step.wait_ms)
                case "wait_xhr": saved[step.save_as] = await wait_xhr(self.page, step.url_pattern, step.method)
                case "extract":  out += extract_group(saved[step.from], step.group, step.map)  # Pydantic 校验
        return out
    async def search(self, kw, sort):         return await self.run_flow("search", keyword=kw, sort=sort)
    async def fetch_item(self, ref):          return await self.run_flow("detail", item_id=ref.item_id)
    async def fetch_comments(self, ref):      return await self.run_flow("comments", item_id=ref.item_id)
```

**轻兜底（不做自动愈合、不回写映射）**：某条 item 的某 group Pydantic 校验失败（字段全空/类型错）→ 仅对该条该 group 回退 LLM 直接抽（喂原始 JSON + schema，Haiku），结果**只用于本次**不回写 yaml；连续多条失败 → WS 提示"该站映射可能过期，建议重新录制 `<flow>`"。运行时几乎零 LLM 成本，兼顾鲁棒性。

### 8.6 adapter.py（仅当录制+LLM 不够）

XHS 签名由浏览器计算、响应拦截即可，**adapter 可缺省**。需 adapter 的场景：多步登录交互（短信验证码）→ override `login()`；字段后处理（时间解析、HTML 清洗）→ override 收尾；需点击"展开更多"才出 API → override `fetch_comments` 加交互。adapter 可调 `super()` 复用 engine。

### 8.7 注册与发现

`registry.py` 启动 `glob('platforms/*/platform.yaml')` → 加载校验 → `{platform: (spec, adapter_cls|None)}`。UI 平台下拉、`scrape_task.platform` 校验、IPC op 路由均从 registry 取。**新增网站 = 录制一个目录，无需改核心代码。**

### 8.8 适用边界（诚实）

现代 SPA（XHS/知乎/公众号 web 等）有数据 XHR，效果最好。**纯 SSR/无 XHR 站点**：`wait_xhr` 换 `wait_selector`+`extract_dom`，LLM 从 HTML 片段映射 CSS——可行但 HTML 体积大、LLM 成本高、效果降级，作兜底而非主路径。

---

## 9. 小红书落地实例（platform.yaml 驱动）

### 9.1 已验证 API 清单（D11 修复，补全 spec 截断）

| 功能 | 端点 | 方法 | 验证 |
| :--- | :--- | :--- | :--- |
| 搜索 | `/api/sns/web/v1/search/notes` | POST (keyword/page/page_size/sort) | ✅ |
| 详情 | `/api/sns/web/v1/feed` | **POST** (source_note_id) | ✅（spec 表头误标 GET，已勘误） |
| 评论 | `/api/sns/web/v2/comment/page` | GET (note_id/cursor) | ✅ v2 |
| 笔记 URL | `/explore/{note_id}` | — | ✅（`/discovery/item/` 已废弃） |
| sort | general/time_descending/popularity_descending | — | ✅ |
| 登录 | 扫码 + 短信（网页版密码登录已移除） | — | ✅ |

签名（X-s/X-t/X-s-common）由 JS 计算 → **必须 API 响应拦截**，不自构请求 → XHS 几乎全配置化。

### 9.2 XHS = 录制三条 flow + 可选 adapter

XHS 作为首个平台，按 §8 流程录制 search/detail/comments 三条 flow（在真 Chrome 各点一遍），LLM 映射生成 `platforms/xiaohongshu/platform.yaml`。签名由浏览器计算、走响应拦截，**adapter 可缺省**。若 comments 的滚动加载 engine 的 `scroll` step 覆盖不了，才写一行 adapter override `fetch_comments`。

### 9.3 抓取五阶段（collection worker 内 `scrape_task` handler，平台无关）

```
Phase 1 暖场 → check_quiet_hours + daily_limit → warmup(2-5 无关页, 30-90s)
Phase 2 搜索 → engine.search(keyword,sort) → 滚动分页 → 关键词间 60-180s
Phase 3 详情 → 去重(platform_id) → engine.fetch_item → 按 download_images 下图 → 笔记间 30-90s
Phase 4 评论 → 按 collect_comments → engine.fetch_comments → 前20按点赞
Phase 5 存储 → upsert SQLite → 写 progress → 更新 last_note_index
```

断点续传：每篇更新 `last_note_index`；resume 从断点续，靠 `platform_id` 去重跳过。

---

## 10. 验证码处理（采集模块）

| 类型 | 策略 | 模块 |
| :--- | :--- | :--- |
| 滑块 | OpenCV 缺口 + 物理轨迹 | `captcha/slide_solver.py` |
| 文字 | ddddocr | `captcha/ocr_solver.py` |
| 点选/短信 | 暂停 + WS 通知人工 | `captcha/manual_handler.py` |

**核心原则**：自动解失败 1 次即暂停，不硬刚。暂停 = worker 写 `paused` result + `ws_events:[captcha_required]` → 用户在真 Chrome 完成 → UI 点"已完成" → web 发新 request 唤醒。文案："请切换到标题为'小红书'的 Chrome 窗口完成验证"。

---

## 11. 错误处理与重试（`core/utils/retry.py`）

沿用 spec §8.1 异常层级（SkimError 基类带 category+fix_hint，子类 Captcha/Ratelimit/PageLoad/Login/DataParse/SessionExpired/AccountBanned/QuietHours/DailyLimit/BrowserClosed/EmptyResult/PortConflict）。

重试：`scraper_retry`（PageLoad/Timeout，3 次指数 3/6/12s 上限 30s）；`rate_limit_retry`（Ratelimit，2 次固定 300s）；不重试 Captcha/Login/AccountBanned/SessionExpired。

异常跨 IPC：worker 捕获 → `IPCResult.error={category,message,fix_hint}` → client 构造 WS error 广播。

账号健康：`fail_count` 连续失败，成功清零，达 5 自动 suspended；`daily_scrape_count` 按日重置；状态机 inactive→active→suspended/banned。

---

## 12. 节律调度（采集 Layer 6）

`scheduler/rhythm.py`：`check_quiet_hours`（22:00-07:00 抛 QuietHoursError）、`check_daily_limit`（超 200 抛 DailyLimitError）、`note_delay`(30-90s)、`keyword_delay`(60-180s)、`should_pause_for_captcha`。`warmup.py`：`random_browse` 2-5 无关页。

> 节律检查在 collection worker 每个 op 开始时执行（以实际发起请求为准），非 Web 侧。

---

## 13. Web UI / FastAPI + WebSocket（`core/ui/` 统一外壳）

### 13.1 外壳与模块路由

- `core/ui/app.py`：FastAPI 工厂，启动遍历 `modules/*/manifest.py` 注册路由，统一导航（采集/分析/制作/运营 tab）。
- 全局首页 `core/ui/routes/dashboard.py`：聚合各模块概览。
- 采集路由（`modules/collection/routes/`）：accounts/tasks/posts/export。`POST /login`、`/api/tasks`、`/resume` 等 → `ipc_client.submit(module="collection", op=...)` 返回 `{request_id,status}`，前端 WS 跟踪；`/cancel` → `ipc_client.cancel`。
- **任务页平台下拉**从 `registry.list_platforms()` 动态生成，新加站点自动出现。

### 13.2 任务执行模型

每模块同时只 1 个 running 长任务（创建时检查）；web `asyncio.create_task` 起 background：轮询 progress→广播；轮询 result→终态处理；worker 死/超时→广播 BrowserClosedError。

### 13.3 WS 统一契约（D7/D8 修复）

所有通知源构造符合 `ProgressMessage` 的 dict 交 `WSManager.broadcast`，废除 `_emit` 位置参数。

```python
class ProgressMessage(BaseModel):
    type: Literal["progress","warn","qr_ready","login_required","login_success",
                  "captcha_required","task_completed","error","disk_warn"]
    module: str | None = None
    task_id: int | None = None; account_id: int | None = None
    message: str; severity: Literal["info","warn","error"] = "info"
    category: str | None = None
    data: dict | None = None        # D8 修复
    timestamp: float
```

WSManager：`connections:set`、`message_buffer:deque(maxlen=50)`、`connect`(回放)、`disconnect`、`broadcast`。worker 不直连 WS，经 ws_events/progress 由 client 代广播。

---

## 14. CSV 导出（采集模块）

沿用 spec §9：AI 模式单文件 `top_comments=作者:内容(N likes)` 管道符；Excel 模式 ZIP 含 posts.csv+comments.csv 按 note_id 关联。`modules/collection/export/csv_exporter.py` 直接读共享 SQLite，不依赖 worker。

---

## 15. 图片下载与磁盘报警

`core/utils/image_downloader.py`：异步下载（默认 `max_concurrency=4`），落 `data/collection/images/<note_id>/`。每次下载前检查：

- `image_disk_warn_gb = 30`（用户指定）：`du` 统计 images 目录，超 30GB → 广播 `disk_warn` WS + UI 红角标，**不中断**；
- `image_disk_stop_gb`（可配，默认 `None`=关）：超阈值则抛 `DiskFullError` 停下载、任务转 paused。

磁盘剩余空间同时检查（`shutil.disk_usage`），剩余 <2GB 也 warn。

---

## 16. 关键时序图

### 16.1 扫码登录

```
UI ──POST /login──▶ web ──submit(collection/login, platform)──▶ requests/<id>.json
                                                              │ (worker 取到)
                                                              ▼
                    web ◀──poll progress──── worker: 拉起 Chrome, 导航 login_url, 截 QR
UI ◀──WS qr_ready── web        (progress: {qr_screenshot, account_id})
                    │           worker: 轮询 success_detect (≤120s)
用户扫码 ────────────────────▶ worker: 检测成功 → 写 result(ok)
web ◀──wait result──┘
UI ◀──WS login_success── web (account.status=active)
```

### 16.2 抓取任务（含异常恢复）

```
UI ──POST /api/tasks──▶ web (检查无 running) ──submit(collection/scrape_task)──▶ requests/<id>.json
                                                                              ▼
web bg task: poll progress + 广播                          worker: warmup→search→detail→comments→存库
UI ◀──WS progress(每篇)── web ◀──progress 文件── worker
                                              │ (Chrome 死) │
web detect worker dead / timeout                          ▼
UI ◀──WS error(BrowserClosed)── web
[重启浏览器] ──POST /resume──▶ web ──submit(resume, last_note_index)──▶ ...
```

### 16.3 验证码暂停 / 16.4 磁盘报警

验证码：worker 解失败 1 次 → `paused` + `captcha_required` → 人工完成 → resume。磁盘：超 30GB → worker 写 `disk_warn` 进 progress → web 广播 → UI 角标，不停任务。

---

## 17. spec [R0] bug 修复汇总

| bug | 修复 |
| :--- | :--- |
| browser_pool 缺 get_page/release_page | IPC 下概念消失（§6.5），scraper 用 attach() 的 ctx（D9） |
| import 不一致 | 统一 `from semilabs_hone.core...` / `from semilabs_hone.modules.collection...` |
| TaskCreate 字段未落表 | scrape_tasks 补两列，handler 必读（D6/§7.1） |
| ProgressMessage 缺 data | 补 `data: dict | None`（D8/§13.3） |
| WS 契约不一致 | 单一 dict 契约（D7/§13.3） |
| resume/关键词/captcha/session | resume 走 IPC op + last_note_index；关键词即 keywords upsert；captcha 走 paused；session 走 validate |
| feed API 方法 | 统一 POST（D10/§9.1） |
| 单产品/单平台 | 升级内容工厂单体仓库（D12）+ 声明化多平台（D13） |

---

## 18. 实施顺序（MVP = core + collection）

1. `pyproject.toml` + `main.py` + `config.py` + `core/utils/{logger,retry}.py`
2. `core/models/`（§7）+ `core/ipc/{protocol,paths,client,server}.py`
3. `core/ui/{app,ws}.py` + base/dashboard 模板 + manifest 注册机制
4. `modules/collection/browser/{cdp,profile,launchagent,worker_main}.py`（§4）
5. `modules/collection/anti_detect/{stealth,human_behavior,fingerprint,ua_pool}.py`（§5）
6. **`modules/collection/scrapers/{base,spec,recorder,llm_mapper,engine,field_extract,registry}.py`** + 录制 XHS 生成 `platforms/xiaohongshu/platform.yaml`（§8 核心）
7. `modules/collection/{captcha,scheduler,export,handlers,routes}/` + 模板 + manifest
8. `tests/{core,collection}/`
9. （未来）analysis/production/operations 各自 manifest+handler+routes 挂同一 core

---

## 19. 新增一个网站的抓取 —— 完整 walkthrough

> 回答"如何灵活又方便地指定抓取内容"。**全程不写 YAML、不碰 DevTools、不写 JSONPath**，在真 Chrome 里点一遍即可。

**场景**：要抓某新站点 `example-site.com` 的搜索结果 + 详情 + 评论。

1. **添加站点 → 录制**：UI 点"添加站点"，输入显示名 → worker 开 Chrome 到目标站。
2. **录 search flow**：在 Chrome 里搜一次（输入关键词、滚动加载更多）→ recorder 捕获 step 链 + 搜索 XHR 样本 → UI 列出捕获的 XHR，你勾选"搜索 XHR → ItemRef"。
3. **录 detail flow**：点开一篇笔记 → recorder 捕获导航 + feed XHR → 勾选"feed XHR → Post（正文+互动）"。
4. **录 comments flow**：滚动评论区 → recorder 捕获 comments XHR → 勾选"comments XHR → Comments"。
5. **录 login flow（如需）**：扫码登录一次，recorder 记录登录类型与成功检测。
6. **LLM 生成映射 + 样本校验**：Haiku 对每个 group 从样本生成 JSONPath，跑样本验证非空；失败的标红让你补一句自然语言描述或重录该步。
7. **存 yaml + 重启** → registry 自动发现 → UI 平台下拉出现新站 → 建任务选它即可抓。

**多步跳转怎么指定？** 不用指定——录制时你点的每一步（首页→分类→帖子→内容页）都被录进 step 链，运行时原样回放（带人类行为模拟）。你可在步骤编辑器删误点的闲步。

**内容页一堆信息（正文/评论/点赞）怎么指定具体块？** 不逐字段点——按 schema group 指派 XHR：`feed_resp→Post`、`cmt_resp→Comments`。group 内所有字段（title/content/likes/collects/...）LLM 自动从该 XHR 映射。要加 schema 之外的字段（如"视频时长"）填一句自然语言描述，LLM 在样本里定位。

**只在以下情况写 `adapter.py`**（覆盖对应方法，其余仍走录制生成的 yaml）：
- 登录交互录制表达不了（短信验证码交互）→ override `login()`；
- 字段需后处理（时间解析、HTML 清洗）→ override 收尾；
- 需点击"展开更多"才出 API → override `fetch_comments()` 加交互。

**改版坏了怎么办**：该站某 flow 校验连续失败 → WS 提示"建议重新录制 example_site 的 detail flow"→ 你重录那一条 flow，其余不动；运行时轻兜底 LLM 让你在重录前还能凑合抓。

---

## 20. 验证方案（设计可验证性）

1. `python -m semilabs_hone serve` → :8530 → 统一 Dashboard、导航含"采集"tab、无账号引导卡；
2. 采集 → 添加账号（平台下拉含 XHS）→ POST /login → IPC request 落盘 → worker 拉起 Chrome → WS qr_ready+QR → 扫码 → login_success+active；
3. 反检测自检：worker 的 Chrome DevTools console `navigator.webdriver`=`undefined`，WebGL getParameter 返回真实 GPU；
4. 新建任务 → WS 实时 progress → task_completed；
5. 数据浏览 → 详情 → 评论（rank 1-20）；
6. 导出 AI/Excel 两模式；
7. 异常恢复：关 Chrome → error(BrowserClosed) → [重启浏览器] → resume 从 last_note_index 续；
8. 节律：22:00 → QuietHoursError；日限额满 → DailyLimitError；
9. 验证码：滑块解失败 1 次 → paused+captcha_required → 人工 → resume；
10. **磁盘报警**：images 目录 >30GB → disk_warn + UI 角标，任务不中断；
11. **多平台**：UI"添加站点"→ 录制一个测试站点的 search/detail/comments → LLM 生成 yaml → 重启 → 平台下拉自动出现 → 可建任务；
12. 单测：test_models/test_api_parser/test_csv_export/test_rhythm/test_retry/test_ipc/test_field_extract/test_routes。

---

## 21. 未决事项（MVP 已尽量收敛）

- **UA 远程库端点**：`UA_STRATEGY=variety` 时需用户自填一个可信 UA 库 URL（默认 `real` 策略不需要）；
- **JSONPath/CSS 解析**：建议引第三方库（`jsonpath-ng` + `selectolax`），省工且稳，编码阶段定；
- **录制 selector 稳定性**：CDP 录制点击的多策略 selector（text/role/aria-label/nth-of-type）运行时回退优先级，编码阶段实测调；
- **LLM 映射模型**：Haiku 档做字段映射/兜底（已定），编码阶段接 `claude-haiku-4-5` 结构化输出；
- **纯 SSR/无 XHR 站点**：`wait_selector`+`extract_dom` 走 HTML→LLM，成本高、降级路径，MVP 不优先；
- **跨模块并发上限**：MVP 不做，待 analysis 上线时按"全厂 N 个 worker 上限"补；
- **分析数据契约**：defer 到 analysis 立项。
