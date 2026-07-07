以下是根据图片内容整理的完整 `SPEC.md` 文本。已按照逻辑顺序拼接，并去除了重复的页眉、页脚及重叠部分。

---

# skim 产品设计与实施规格书

## 1. 概述

### 1.1 产品定位
skim 是一个本地桌面应用，从小红书抓取内容素材（笔记+评论），存入 SQLite，支持 CSV 导出供 AI 分析和 Excel 筛选。面向内容创作者，解决手工刷平台搜索素材效率低下的问题。

### 1.2 MVP 范围
- **支持**：小红书关键词搜索抓取、SQLite 存储、CSV 导出、本地 Web UI
- **不含**：AI 分析、定时任务、微信公众号/知乎等其他平台

### 1.3 项目路径
`/home/admin/workspace/skim/`

### 1.4 参考实现
- `cargo-tracker` (`/home/admin/workspace/cargo-tracker/`)：反检测、验证码、浏览器管理等模块的复用来源
- MediaCrawler (开源)：小红书爬虫的架构参考

---

## 2. 技术栈

| 层 | 技术选型 | 理由 |
| :--- | :--- | :--- |
| 前端 | Jinja2 + HTMX + Pico CSS | 轻量无框架依赖，HTMX 处理动态交互 |
| 后端 | FastAPI + Uvicorn | 异步、WebSocket 支持好 |
| 爬虫 | Playwright (CDP 模式) | 复用 cargo-tracker 成熟方案 |
| 数据库 | SQLAlchemy + SQLite | 单文件、零运维 |
| 验证码 | ddddocr + OpenCV | 复用 cargo-tracker 方案 |

```txt
# requirements.txt
playwright, playwright-stealth    # 浏览器自动化
fastapi, uvicorn, jinja2          # Web UI
sqlalchemy, pydantic              # 数据层
loguru, tenacity                  # 日志+重试
ddddocr, opencv-python-headless   # 验证码
httpx, python-dotenv, numpy       # 工具库
websockets, python-multipart      # WebSocket + 文件上传
cryptography                      # Cookie 加密(预留)
```

---

## 3. 模块架构

### 3.1 目录结构

```text
skim/
├── main.py                 # 入口: CLI + FastAPI 启动
├── config.py               # 环境配置
├── requirements.txt
│
├── ui/
│   ├── app.py              # FastAPI 应用工厂 + WSManager
│   ├── routes/
│   │   ├── dashboard.py    # GET / 首页
│   │   ├── accounts.py     # 账号管理 API + 页面
│   │   ├── tasks.py        # 抓取任务 API + 页面
│   │   ├── posts.py        # 内容浏览页面
│   │   ├── export.py       # CSV 导出 API
│   │   └── ws.py           # WebSocket 端点
│   ├── templates/          # Jinja2 HTML (base + 6 页面)
│   └── static/             # style.css + app.js
│
├── scrapers/               # 平台爬虫
│   ├── base.py             # 抽象 BasePlatformScraper
│   ├── registry.py         # @register_platform 装饰器注册
│   └── xiaohongshu/
│       ├── scraper.py      # 编排: login->search->detail->comments
│       ├── login.py        # 扫码登录 + Cookie 恢复/导入
│       ├── search.py       # 关键词搜索 (API拦截+DOM兜底)
│       ├── detail.py       # 笔记详情提取
│       ├── comments.py     # 评论提取 (前20条)
│       ├── selectors.py    # CSS/XPath 选择器 (多fallback)
│       └── api_parser.py   # XHR JSON 解析器
│
├── anti_detect/            # 反检测
│   ├── stealth.py          # JS注入: Canvas/Audio噪声, navigator伪装
│   ├── human_behavior.py   # 人类行为模拟: 打字/点击/滚动/滑块
│   └── fingerprint.py      # 一账号一固定指纹管理
│
├── captcha/                # 验证码
│   ├── solver.py           # 自动检测类型+分发求解
│   ├── slide_solver.py     # 滑块验证码 (OpenCV缺口检测)
│   ├── ocr_solver.py       # 文字验证码 (ddddocr)
│   └── manual_handler.py   # 暂停+WebSocket通知用户手动处理
│
├── models/                 # 数据模型
│   ├── db.py               # Engine, Session, init_db()
│   ├── account.py, keyword.py, task.py, post.py, comment.py
│   └── schemas.py          # Pydantic 校验 (API I/O)
│
├── export/
│   └── csv_exporter.py     # CSV 导出 (AI模式 + Excel模式)
│
├── utils/
│   ├── retry.py            # 异常层级 + tenacity 重试装饰器
│   ├── image_downloader.py # 异步图片下载 (含磁盘空间检查)
│   └── logger.py           # loguru 配置
│
├── data/                   # 运行时数据 (gitignored)
│   ├── skim.db
│   ├── images/<note_id>/
│   ├── exports/
│   ├── profiles/<account_id>/  # Chrome user-data-dir
│   ├── logs/
│   └── debug/              # DOM 快照
│
└── tests/
    ├── fixtures/           # 模拟 API 响应 JSON
    ├── test_models.py
    ├── test_api_parser.py
    ├── test_csv_export.py
    ├── test_rhythm.py
    ├── test_retry.py
    └── test_routes.py
```

### 3.2 数据流

```text
用户 (浏览器 localhost:8530)
    │
    ├─ 1. 添加账号 -> 分配固定指纹 + 创建 Chrome profile 目录
    ├─ 2. 扫码登录 -> 启动 Chrome(CDP) -> XHS 登录页 -> 用户扫码 -> Cookie 持久化
    ├─ 3. 创建抓取任务 -> 输入关键词 / 排序 / 每词篇数
    ├─ 4. 执行抓取:
    │     暖场(2-5页) -> 搜索(API拦截) -> 详情(API拦截+DOM兜底)
    │       -> 下载图片 - 抓评论 -> 存SQLite -> WebSocket推送进度
    ├─ 5. 浏览数据 -> 按关键词/日期筛选
    └─ 6. 导出 CSV -> AI模式(单文件含评论) 或 Excel模式(分表ZIP)
```

---

## 4. 数据模型

### 4.1 accounts 表
```sql
id INTEGER PK, platform VARCHAR(20) DEFAULT 'xiaohongshu',
phone VARCHAR(20), nickname VARCHAR(100),
login_method VARCHAR(20) DEFAULT 'qrcode', -- qrcode | cookie_import
profile_dir VARCHAR(255), user_agent VARCHAR(500),
viewport_w INTEGER DEFAULT 1920, viewport_h INTEGER DEFAULT 1080,
status VARCHAR(20) DEFAULT 'inactive', -- inactive | active | suspended | banned
last_login_at DATETIME, last_scrape_at DATETIME,
daily_scrape_count INTEGER DEFAULT 0, total_scrape_count INTEGER DEFAULT 0,
fail_count INTEGER DEFAULT 0, notes TEXT,
created_at DATETIME, updated_at DATETIME
```

### 4.2 keywords 表
```sql
id INTEGER PK, text VARCHAR(200) NOT NULL,
platform VARCHAR(20) DEFAULT 'xiaohongshu',
use_count INTEGER DEFAULT 0, last_used_at DATETIME, created_at DATETIME
UNIQUE(text, platform)
```

### 4.3 scrape_tasks 表
```sql
id INTEGER PK, account_id INTEGER FK(accounts.id),
platform VARCHAR(20) DEFAULT 'xiaohongshu',
status VARCHAR(20) DEFAULT 'pending', -- pending | running | completed | failed | cancelled
max_posts_per_keyword INTEGER DEFAULT 20,
posts_scraped INTEGER DEFAULT 0, last_note_index INTEGER DEFAULT 0,
sort_type VARCHAR(30) DEFAULT 'general', -- general | time_descending | popularity_descending
error_message TEXT, error_category VARCHAR(30),
started_at DATETIME, completed_at DATETIME, created_at DATETIME
```

### 4.4 task_keywords 表
```sql
task_id INTEGER FK, keyword_id INTEGER FK
PRIMARY KEY(task_id, keyword_id)
```

### 4.5 posts 表
```sql
id INTEGER PK, platform VARCHAR(20), platform_id VARCHAR(100) NOT NULL,
task_id INTEGER FK, keyword_id INTEGER FK,
url TEXT, title TEXT, author_id VARCHAR(100), author_name VARCHAR(200),
content TEXT, post_type VARCHAR(20), -- normal | video
image_count INTEGER DEFAULT 0, image_urls TEXT(JSON), local_images TEXT(JSON),
likes INTEGER DEFAULT 0, collects INTEGER DEFAULT 0,
comments_count INTEGER DEFAULT 0, shares INTEGER DEFAULT 0,
tags TEXT(JSON), published_at DATETIME, scraped_at DATETIME, raw_json TEXT,
created_at DATETIME
UNIQUE(platform, platform_id) -- 去重: 同笔记多次发现时 upsert
```

### 4.6 comments 表
```sql
id INTEGER PK, post_id INTEGER FK(posts.id) NOT NULL,
platform_id VARCHAR(100), author_name VARCHAR(200),
content TEXT NOT NULL, likes INTEGER DEFAULT 0,
sub_comment_count INTEGER DEFAULT 0, is_author_liked BOOLEAN DEFAULT FALSE,
published_at DATETIME, scraped_at DATETIME,
rank INTEGER, raw_json TEXT, created_at DATETIME
UNIQUE(post_id, platform_id)
```

### 4.7 关键设计决策
- **`platform_id` 去重**: 同一笔记多关键词发现时 upsert，只更新互动数据
- **`raw_json` 保留**: 存原始 API 响应，防平台改接口丢数据，兼顾未来 AI 分析
- **`last_note_index`**: 任务断点续传，失败后从此处恢复

---

## 5. 小红书抓取流程

### 5.1 XHS Web 架构要点
小红书 Web 版 (`www.xiaohongshu.com`) 是 React SPA，关键 API:

| 功能 | API | 方法 |
| :--- | :--- | :--- |
| 搜索笔记 | `/api/sns/web/v1/search/notes` | POST (body: keyword/page/page_size/sort) |
| 笔记详情 | `/api/sns/web/v1/feed` | POST (body: source_note_id) - 注意是 POST 非 GET |
| 评论 | `/api/sns/web/v2/comment/page` | GET (query: note_id/cursor) |

请求签名 (`X-s`, `X-t`, `X-s-common`) 由客户端 JS 计算，采用 **API 响应拦截** (`page.on("response")`) 而非自构请求—浏览器原生计算签名。
**已验证**: 上述端点、URL 格式 `/explore/{note_id}`、sort 参数 (`general`/`time_descending`/`popularity_descending`)。

### 5.2 登录流程
小红书网页版已移除密码登录，采用三级策略:

```text
Level 1: Cookie 自动恢复 (Chrome profile 中的 Cookie 仍有效)
  ↳ 失败
Level 2: 扫码登录 (有头浏览器展示 QR 码，用户用小红书 App 扫码)
  ↳ 不方便
Level 3: Cookie 手动导入 (用户从浏览器 DevTools 导出 Cookie JSON 粘贴到 UI)
```

**扫码登录实现** (`scrapers/xiaohongshu/login.py`):
1. 导航到 XHS 登录页 -> 页面展示 QR 码
2. WebSocket 通知用户 "请在 Chrome 窗口中用小红书 App 扫码"
3. 轮询检测登录成功 (URL 跳转 / 用户信息 API / 用户头像出现)
4. 超时 120s 未扫码则失败
5. 登录成功 -> Cookie 自动持久化在 Chrome profile

### 5.3 抓取流程

```text
Phase 1: 暖场 -> 检查安静时段(22-07)和日限额 -> 浏览2-5个无关页面(30-90s)
Phase 2: 搜索 -> 导航搜索页 -> page.on("response") 拦截 API -> 解析笔记列表
  - API 失败则 DOM 解析兜底 -> 滚动分页 -> 关键词间隔 60-180s
Phase 3: 详情 -> 去重检查(platform_id) -> 导航笔记页 -> 拦截 feed API
  - DOM 兜底 -> 下载图片 -> 笔记间隔 30-90s
Phase 4: 评论 -> 滚动评论区触发加载 -> 拦截 comment API -> 取前20条(按点赞排序)
Phase 5: 存储 -> upsert 到 SQLite -> WebSocket 推送进度
```

**断点续传**: 每成功抓取一篇即更新 `last_note_index`，任务失败后提供 [继续] 按钮，从断点恢复。通过 `platform_id` 去重跳过已抓取笔记。

---

## 6. 反检测架构 (六层防护)

### Layer 1: 干净 Chrome + CDP 接管
- `subprocess` 启动系统真实 Chrome，带 `--remote-debugging-port`
- 不带任何 automation flag
- Playwright `connect_over_cdp()` 接管
- 效果: `navigator.webdriver === undefined`, 无 CDP 痕迹
- **复用**: `cargo-tracker/utils/browser_pool.py` (lines 96-156)

### Layer 2: 一账号一固定指纹
- 账号创建时随机分配并永久固定: UA, viewport, color scheme
- 存入 `accounts` 表，每次连接时加载
- Chrome `--user-data-dir` 按账号隔离 -> Cookie/localStorage 自然持久化
- 不随机化 -> 降低"异设备登录"风控

### Layer 3: 最小化 Stealth 注入 (CDP 模式)
- CDP 模式下只注入 Canvas/AudioContext 噪声脚本
- 不注入完整 stealth 脚本 -> 真 Chrome 的 navigator/WebGL 已经是真的
- **复用**: `cargo-tracker/anti_detect/stealth.py`

### Layer 4: 人类行为模拟
- `human_type()`: 逐字符 50-200ms 延迟，5% 概率长停顿
- `human_click()`: 贝塞尔曲线鼠标移动 + 随机偏移
- `generate_slide_track()`: 先加速后减速 + 回弹
- `random_scroll()`, `random_browse()`: 暖场用
- **复用**: `cargo-tracker/anti_detect/human_behavior.py`

### Layer 5: API 拦截优先 + DOM 兜底
```python
api_future = asyncio.get_running_loop().create_future()
page.on("response", lambda resp: self._capture_api(resp, api_future))
await page.goto(url)
try:
    data = await asyncio.wait_for(asyncio.shield(api_future), timeout=15)
    return self._parse_api(data)
except asyncio.TimeoutError:
    return await self._parse_dom(page)
```
- **复用**: `cargo-tracker/scrapers/airchina_cargo.py` (lines 286-329)

### Layer 6: 节律调度器
- 暖场: 抓取前浏览 2-5 个无关页面 (30-90s)
- 笔记间隔: 30-90s 随机 | 关键词间隔: 60-180s 随机
- 日限额: 单账号每天最多 200 篇 (可配置)
- 安静时段: 22:00-07:00 不跑
- 验证码策略: 自动求解失败 1 次即暂停 + 通知用户

---

## 7. 验证码处理

| 类型 | 策略 | 实现 |
| :--- | :--- | :--- |
| 滑块验证码 | 自动: OpenCV 缺口检测 + 模拟人类拖拽 | `captcha/slide_solver.py` |
| 文字验证码 | 自动: ddddocr 识别 | `captcha/ocr_solver.py` |
| 图片点选/短信验证 | 手动: 暂停 + WebSocket 通知用户 | `captcha/manual_handler.py` |

**核心原则**: 自动求解失败 1 次即暂停，不硬刚 (账号比脚本值钱)。通知中明确指引: "请切换到标题为'小红书'的 Chrome 窗口完成验证"。

---

## 8. 错误处理

### 8.1 异常层级
```text
SkimError (base, 附带 category + fix_hint)
├── CaptchaError        -> 暂停 + 通知用户手动处理
├── RatelimitError      -> 等 5 分钟自动重试
├── PageLoadError       -> 3 次指数退避重试
├── LoginError          -> 通知用户重新登录
├── DataParseError      -> 跳过该篇 + 保存 raw_json
├── SessionExpiredError -> 暂停 + 提示扫码重新登录
├── AccountBannedError  -> 标记账号 banned + 终止任务
├── QuietHoursError     -> 提示等到 07:00
├── DailyLimitError     -> 提示明天再试
├── BrowserClosedError  -> 暂停 + 提示重新启动浏览器
├── EmptyResultError    -> 跳过该关键词 + 连续空结果则警告
└── PortConflictError   -> 自动切换端口 9333-9340
```

每个异常附带中文 `fix_hint`，通过 WebSocket 推送到 UI。

### 8.2 重试策略
- `scraper_retry`：对 PageLoadError/TimeoutError 重试 3 次，指数退避 (3s/6s/12s，上限 30s)
- `rate_limit_retry`：对 RateLimitError 重试 2 次，固定等待 300s
- 不重试：CaptchaError, LoginError, AccountBannedError (需用户干预)

### 8.3 账号健康监控
- `fail_count`：连续失败次数，成功后清零，达 5 次自动 suspended
- `daily_scrape_count`：运行时检查，按日判断是否需要重置
- 状态机：`inactive` → `active` → `suspended/banned`

### 8.4 关键异常场景处理

| 场景 | 处理 |
| :--- | :--- |
| Cookie 过期 (高频) | validate_session → 暂停任务 → 通知扫码 → 扫码后从断点继续 |
| Chrome 窗口被关闭 | 捕获 CDP 断连 → BrowserClosedError → 暂停 + [重启浏览器] 按钮 |
| 空搜索结果 | 检查页面“没有找到”文案区分真无结果 vs 被限流，连续 3 个空则警告 |
| CDP 端口冲突 | 启动前检查端口，被占用则自动尝试 9334-9340 |
| 并发任务 | MVP 同时只允许一个 running 任务，创建时检查 |
| WebSocket 断线 | 页面加载时检查 running 任务，自动重连，回放最近 50 条消息 |

---

## 9. CSV 导出格式

### 9.1 AI 模式 (单文件，帖子+评论合一)

| 字段 | 说明 |
| :--- | :--- |
| note_id, url | 平台 ID + 链接 |
| title, author, content | 标题、作者、正文 |
| tags | 管道符分隔 |
| post_type | normal / video |
| likes, collects, comments_count, shares | 互动数据 |
| published_at, keyword, image_count | 元数据 |
| top_comments | 格式：`作者:内容(N likes)` 管道符分隔 |
| scraped_at | 抓取时间 |

### 9.2 Excel 模式 (ZIP 含两个 CSV)
- `posts.csv`：一行一篇笔记，所有字段展开
- `comments.csv`：一行一条评论，通过 note_id 关联

---

## 10. main.py 规格

```python
"""skim - 内容素材抓取工具
Usage:
    python main.py serve              # 启动 Web UI (默认)
    python main.py serve --port 8530  # 指定端口
"""
```

- 默认命令 `serve`：`init_db()` + `setup_logger()` + uvicorn 启动 FastAPI app
- `argparse`：`serve` (默认), `version`
- `serve` 参数：`--port` (默认 8530), `--host` (默认 127.0.0.1)

---

## 11. FastAPI 应用规格 (ui/app.py)

```python
def create_app() -> FastAPI:
    app = FastAPI(title="skim")
    app.mount("/static", StaticFiles(...))
    # 注册路由、startup/shutdown 事件、Jinja2 模板
    return app
```

- startup: `init_db()`, `setup_logger()`
- 全局异常：捕获 SkimError → JSON `{error, category, fix_hint}`

---

以下是根据图片内容整理的完整 `SPEC.md` 文本。已按照逻辑顺序拼接，并去除了重复的页眉、页脚及重叠部分。

---

### 12. REST API 端点规格

#### 12.1 账号管理 `ui/routes/accounts.py`

| Method | Path | 功能 | Request | Response |
| :--- | :--- | :--- | :--- | :--- |
| GET | `/accounts` | 账号管理页面 | - | HTML |
| POST | `/api/accounts` | 创建账号 | `{platform, nickname}` | `{id, status, ...}` |
| DELETE | `/api/accounts/{id}` | 删除账号 | - | `{ok}` |
| POST | `/api/accounts/{id}/login` | 扫码登录 | - | `{status}` |
| POST | `/api/accounts/{id}/import-cookies` | 导入 Cookie | `{cookies_json}` | `{status}` |
| POST | `/api/accounts/{id}/validate` | 验证会话 | - | `{valid: bool}` |

**登录流程 WS:**
1. `POST login` → 后端启动 BrowserPool → 导航 XHS 登录页
2. WebSocket 通知 "请在 Chrome 窗口扫码"
3. 轮询检测登录成功
4. 成功 → account.status = "active", 通知 "登录成功"

#### 12.2 抓取任务 `ui/routes/tasks.py`

| Method | Path | 功能 | Request | Response |
| :--- | :--- | :--- | :--- | :--- |
| GET | `/tasks/new` | 新建任务页面 | - | HTML |
| GET | `/tasks/{id}` | 任务进度页面 | - | HTML |
| POST | `/api/tasks` | 创建新任务 | `{account_id, keywords[], max_posts_per_keyword, sort_type, download_images, collect_comments}` | `{id, status}` |
| POST | `/api/tasks/{id}/cancel` | 取消任务 | - | `{status}` |
| POST | `/api/tasks/{id}/resume` | 恢复失败任务 | - | `{status}` |

- 同一时间只允许一个 running 任务
- 任务在 `asyncio.create_task` 中后台执行
- 通过 WebSocket 推送进度
- **注意**: `TaskCreate` schema (`schemas.py:34-40`) 含 `download_images`/`collect_comments` 字段, 但 `scrape_tasks` 表无对应列, `run_task` 也不读取。需补：要么在表中加列，要么在 task_meta JSON 中存储。

#### 12.3 内容浏览 `ui/routes/posts.py`

| Method | Path | 功能 | Query |
| :--- | :--- | :--- | :--- |
| GET | `/posts` | 内容列表 | `?keyword&page=` |
| GET | `/posts/{id}` | 笔记详情 | - |

#### 12.4 数据导出 `ui/routes/export.py`

| Method | Path | Query | Response |
| :--- | :--- | :--- | :--- |
| GET | `/api/export` | `?task_id=&keyword=&format=xlsx\|excel` | 文件下载 |

#### 12.5 Dashboard `ui/routes/dashboard.py`

| Method | Path | 功能 |
| :--- | :--- | :--- |
| GET | `/` | 首页：统计概览 + 最近任务, 无账号时显示引导卡片 |

---

### 13. WebSocket 协议 `ui/routes/ws.py`

**端点**: `ws://localhost:8530/ws`

#### 消息格式

```json
{
  "type": "progress",
  "task_id": 12,
  "message": "正在抓取第3/10页: 提拉米苏分享",
  "data": { "posts_scraped": 3, "total_posts": 30, "current_keyword": "咖啡拉花" }
}
```

**注意**: `ProgressMessage` schema (`schemas.py:96-102`) 当前缺 `data` 字段, 只有 type/task_id/message/severity/category/timestamp。需补 `data: dict | None = None`, 见下方。

#### 消息类型

| type | 触发时机 | data |
| :--- | :--- | :--- |
| `progress` | 每抓一篇笔记 | posts_scraped, total_posts, current_keyword, current_note |
| `warn` | 限流预警中 | message |
| `qr_ready` | 扫码登录 QR 码已生成 | account_id, message ("请在Chrome窗口扫码") |
| `login_required` | Cookie 过期需要重新登录 | account_id |
| `login_success` | 登录成功 | account_id |
| `captcha_required` | 验证码手动处理 | captcha_type, account_id, message |
| `task_completed` | 任务完成 | posts_scraped, comments_count, images_count |
| `error` | 错误发生 | category, fix_hint, severity |

**通知规范统一 (修复项 #3)**: 当前存在两条不一致的通知路径 --
- `scraper.__init__(callback, msg_type, message, **kwargs)`: 位置参数回调
- `captcha/manual_handler.ManualCaptchaHandler.request_manual_solve`: 调 `ws_manager.broadcast(dict)`
- `login.qrcode_login(ws_notify_callback)`: 调 `callback("qr_ready", message)`

需统一为单一契约: scraper/login/manual_handler 都构造符合 `ProgressMessage` 的 dict 交给 `WSManager.broadcast(dict)`; 废除 `_emit` 的位置参数风格, 或让 `_emit` 内部转成 dict 再 broadcast。

#### WSManager

```python
class WSManager:
    connections: set[WebSocket]
    message_buffer: deque(maxlen=50)
    async def connect(ws): ...      # 新连接时回放 buffer
    async def disconnect(ws): ...
    async def broadcast(msg): ...   # 广播 + 存入 buffer
```

---

### 14. UI 页面规格

#### 技术约束
- Jinja2 服务端渲染 + HTMX 动态交互 (无 React/Vue)
- Pico CSS 基础样式 + 自定义 style.css
- app.js: WebSocket 管理 + 进度更新 + 通知

#### 14.1 base.html
- 顶部导航: skim logo + [首页] [账号] [新建任务] [内容]
- 主内容区: `{% block content %}`
- 底部: WebSocket 连接状态指示器
- 引入 pico.min.css + style.css + app.js

#### 14.2 dashboard.html `/`
- 无账号: 引导卡片 "添加第一个账号开始使用" + [添加账号] 按钮
- 有账号: 统计概览 (总抓取数、总抓取数) + 最近 5 个任务列表 (带状态标签)

#### 14.3 accounts.html `/accounts`
- 账号卡片列表: 平台 + 昵称 + 状态标签 (绿/灰/红) + 上次登录 + 今日抓取数
- 操作: [扫码登录] [导入Cookie] [验证会话] [删除]
- [添加账号] → dialog: 平台下拉 + 备注名 → POST `/api/accounts`

#### 14.4 task_new.html `/tasks/new`
- 表单: 账号下拉 (只显示 active) + 关键词多行文本框 + 每词最大篇数 + 排序 (综合/最新/最热)
- [开始抓取] → POST `/api/tasks` → 跳转进度页

#### 14.5 task_detail.html `/tasks/{id}`
- 信息头: 关键词 + 账号 + 排序 + 状态
- 进度条: posts_scraped / total_posts
- 当前状态文字 + 实时日志区 (scrollable, 最新在上)
- 通知区 (验证码/错误, 红色背景)
- 按钮: running=[取消], failed=[继续], completed=[导出CSV][查看内容]
- JS: WebSocket 驱动进度条和日志更新

#### 14.6 posts.html `/posts`
- 筛选: 关键词下拉 + [搜索] (HTMX 局部刷新) + [导出CSV]
- 笔记卡片: 标题 (可点击) + 作者 + 日期 + likes + collects + comments + 标签
- 分页器

#### 14.7 post_detail.html `/posts/{id}`
- 笔记元信息 + 正文 + 图片网格 (本地缩略) + 评论列表 (rank 1-20)

#### 14.8 static/app.js

```javascript
// WebSocket 连接 + 自动重连
const ws = new WebSocket('ws://${location.host}/ws');
ws.onclose = () => setTimeout(reconnect, 3000);
ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    switch(msg.type) {
        case 'progress': updateProgressBar(msg.data); appendLog(msg.message); break;
        case 'error': showNotification(msg.data.fix_hint, 'error'); break;
        case 'captcha_required': showNotification(msg.message, 'warning'); break;
        case 'task_completed': updateTaskStatus('completed'); break;
        case 'login_success': location.reload(); break;
    }
};
```

---

### 15. 从 cargo-tracker 复用清单

| 源文件 | 复用到 skim | 说明 |
| :--- | :--- | :--- |
| `anti_detect/human_behavior.py` | `anti_detect/human_behavior.py` | 适配复用, 新增 random_scroll/browse |
| `anti_detect/stealth.py` | `anti_detect/stealth.py` | 适配复用, 拆出 NOISE_ONLY_SCRIPT |
| `utils/browser_pool.py:96-156` | `browser/cdp.py` | CDP 启动 + connect 模式 |
| `utils/browser_pool.py:267-309` | `browser/pool.py` | CDP 上下文 + 噪声注入 |
| `scrapers/airchina_cargo.py:98-258` | `scrapers/xiaohongshu/search.py` 等 | API 拦截 + DOM 兜底模式 |
| `utils/retry.py` | `utils/retry.py` | 异常层级 + tenacity 重试 |
| `captcha/slide_solver.py` | `captcha/slide_solver.py` | 滑块验证码 CV 求解 |
| `captcha/ocr_solver.py` | `captcha/ocr_solver.py` | ddddocr 文字识别 |
| `models/db.py` | `models/db.py` | SQLAlchemy + SQLite 初始化 |

---

### 16. 实施现状

#### 已完成

| 模块 | 文件 | 状态 |
| :--- | :--- | :--- |
| config | `config.py` | ✅ |
| models | `models/db,account,keyword,task,post,comment,schemas.py` | ✅ |
| anti_detect | `anti_detect/stealth,human_behavior,fingerprint.py` | ✅ |
| captcha | `captcha/getwuy_slide_solver,ocr_solver,manual_handler?.py` | ✅ |
| browser | `browser/cdp,pool,profile.py` | ✅ |
| scheduler | `scheduler/rhythm,warmup.py` | ✅ |
| scrapers | `scrapers/base,registry.py` + `scrapers/xiaohongshu/*.py` | ✅ |
| export | `export/csv_exporter.py` | ✅ |
| utils | `utils/retry,logger,image_downloader.py` | ✅ |

#### 待实现

| 优先级 | 文件 | 说明 |
| :--- | :--- | :--- |
| P0 | `main.py` | CLI 入口 + uvicorn 启动 |
| P0 | `ui/app.py` | FastAPI 应用工厂 + WSManager |
| P0 | `ui/routes/{dashboard,accounts,tasks,posts,export,ws}.py` | 5 个路由 + WebSocket |
| P0 | `ui/templates/{base,dashboard,accounts,task_new,task_detail,posts,post_detail}.html` | 7 个模板 |
| P0 | `ui/static/{style.css,app.js}` | 样式 + 前端交互 |
| P1 | `tests/` | 单元测试 + 集成测试 |

#### 待修复 (黑值检查发现, 实施前必须处理 — 详见 Section 20)

- **[R0]** 接口不匹配: `browser_pool` 缺少 `get_page(account)` 和 `release_page(self, page)`; 只是 `start(account_id, fingerprint...)` 返回 `(page, ctx)`。`scraper/xiaohongshu/scraper.py:50,56` 调用 `self.browser_pool.get_page(account)` 和 `self.browser_pool.release_page(page)` 会报错。需补 `get_page`/`release_page` 或改 scraper 用 `start()` 直接拿 page。
- **[R0]** 修复方向: 在 `BrowserPool` 上补 `get_page(account)` (内部: 带着 start 刚用 account.id 从 account.user_agent/fingerprint_wv/viewPort_h 构建 fingerprint dict 调 start 从返回...
- **[R0]** Import 路径不一致: ...

*(注：此处图片内容截断，后续为具体 import 路径问题描述)*

---

### 17. 测试计划

**框架**: pytest + pytest-asyncio

#### 17.1 tests/test_models.py
- `test_create_account()`: 创建账号, 验证默认值
- `test_post_export()`: 同 platform_id 二次插入应更新
- `test_task_lifecycle()`: pending → running → completed
- `test_task_resume()`: failed 后 last_note_index 保留

#### 17.2 tests/test_xpl_parser.py
- `test_parse_search_results()`: 验证提取 note_id, title, likes
- `test_parse_note_detail()`: 完整字段提取
- `test_parse_comments()`: 评论排序和字段
- `test_parse_empty_response()`: 空响应不崩溃
- `test_parse_malformed_json()`: 缺字段时用默认值

#### 17.3 tests/test_csv_export.py
- `test_ai_mode_format()`: 列头, top_comments 管道符格式
- `test_excel_mode_zip()`: ZIP 含 posts.csv + comments.csv
- `test_export_empty_db()`: 空库不崩溃

#### 17.4 tests/test_rhythm.py
- `test_quiet_hours_block()`: 22:00-07:00 抛 QuietHoursError
- `test_daily_limit()`: 超限抛 DailyLimitError
- `test_random_delay_range()`: 延迟在 min-max 内

#### 17.5 tests/test_retry.py
- `test_error_hierarchy()`: 所有异常是 SkimError 子类
- `test_fix_hint_present()`: 每个异常有 fix_hint

#### 17.6 tests/test_routes.py (FastAPI TestClient)
- `test_create_account()`: POST /api/accounts
- `test_dashboard_empty()`: 无账号时显示引导
- `test_create_task_no_account()`: 无 active 账号报错
- `test_export_csv()`: GET /api/export 返回文件

#### 测试 fixtures
`tests/fixtures/` `search_response.json`, `detail_response.json`, `comments_response.json`

---

### 18. 实施顺序 (文件依赖)

Step 0: [前置] 完成 Section 16 全部 9 项修复 (致命: pool 方法 + import 统一; 高: resume/关键词持久化/captcha 嵌入/session; 中: 重试/callback/并发)
Step 1: main.py
Step 2: ui/app.py (FastAPI + WSManager)
Step 3: ui/routes/ws.py
Step 4: ui/routes/dashboard.py + templates/base.html + templates/dashboard.html
Step 5: ui/routes/accounts.py + templates/accounts.html
Step 6: ui/routes/tasks.py + templates/task_new.html + templates/task_detail.html
Step 7: ui/routes/posts.py + templates/posts.html + templates/post_detail.html
Step 8: ui/routes/export.py
Step 9: ui/static/style.css + ui/static/app.js
Step 10: tests/

---

### 19. 验证方案

1. `python main.py serve` → localhost:8530 → Dashboard 页面
2. 添加账号 → 扫码账号 → 状态变 Active
3. 新建任务(关键词) → 进度实时更新 → 完成
4. 数据浏览: 内容列表 → 笔记详情 → 评论列表
5. 数据导出: AI 模式 CSV → 文本+评论管道符格式
6. 异常恢复: 关闭 Chrome → 错误通知 → 点继续恢复

---

### 20. 黑值检查室结论

#### 20.1 审查方法
对方案中的技术假设做了三方交叉验证: (1) 对照已实现代码核实接口/签名/字段是否真实存在; (2) 核查小红书平台实际行为 (API 端点/URL/登录方式/限流); (3) 链接编排超逻辑寻找设计缺陷。

#### 20.2 已验证为真 (高置信度, 无需改动)

| 验证项 | 结论 |
| :--- | :--- |
| XHS 搜索 API `POST /api/sns/web/v1/search/notes` | ✅ 正确, 锚点稳定 |
| XHS 详情 API `GET /api/sns/web/v1/feed` | ✅ 颜色正确 (方法为 POST, 已在 9.1 修正) |
| XHS 评论 API `GET /api/sns/web/v2/comment/page` | ✅ 正确, 取 v2 |
| 笔记 URL `/explore/{note_id}` | ✅ 正确, `/discovery/item/` 已废弃 |
| sort 参数 `"general"`/`"time_descending"`/`"popularity_descending"` | ✅ 正确 |
| 网页版密码登录已移除, 仅扫码+短信

