# USER_SOP — 用户操作 SOP 推演 + 第二轮设计 Review

> 2026-07-29。站在**真实使用者**视角，把"打开系统 → 选平台 → 登录 → 搜关键词 → 抓数据 → 看数据 → 导出 → 异常恢复"整条 SOP 逐步推演，
> 每一步对照 UI / API / IPC / worker / DB / WS 六层实现，找出**用户会真实撞到**的缺口。
> 本文是 SOP 的规范来源；缺口清单（G1–G18）是第二轮修复的跟踪表。
>
> 上一轮（设计文档 review 17 项）见 [FIX_PLAN.md](FIX_PLAN.md)。

## 0. 角色与前置

- 单用户本机运行（macOS），自己的社媒账号，自用素材采集。
- 启动：`python -m semilabs_hone serve` → 浏览器打开 `http://127.0.0.1:8530`。
- 一账号 = 一 Chrome profile = 一 worker 进程；web 进程只发指令，不碰浏览器。

---

## 1. SOP 全流程（目标态）

### S1 打开首页

看到：模块导航（采集）、账号数/任务数/素材数、当前运行中的任务入口；零账号时给引导卡"先去添加账号"。

| 层 | 目标行为 |
|---|---|
| UI | `GET /` dashboard，卡片 = 账号数 / 任务数 / 素材数 + 运行中任务链接 |
| DB | 只读计数 |

### S2 选平台 + 建账号

看到：平台下拉（来自 registry，含内置 xiaohongshu + 用户自己录制的站点）；填昵称 → 提交 → 列表**原地刷新**出现新账号，状态 `inactive`。

| 层 | 目标行为 |
|---|---|
| UI | HTMX 提交后只替换账号列表片段（不是整页塞进 div） |
| API | `POST /api/accounts` → 抽指纹（一账号一固定）+ 建 profile 目录 |
| DB | accounts 行含 viewport/color_scheme/timezone/locale/profile_dir |

### S3 登录（扫码）

点"登录" → web 拉起该账号的 worker（真 Chrome，独立 profile）→ worker 打开平台登录页 → **UI 上出现二维码截图**（也可以直接在弹出的 Chrome 窗口里扫）→ 手机扫码 → URL 跳回首页 → worker 判定成功 → 账号状态 `active` + toast "登录成功"。

| 层 | 目标行为 |
|---|---|
| API | `POST /api/accounts/{id}/login`，platform **取该账号的 platform**（不是硬编码） |
| worker | Tier1 会话恢复 → Tier2 扫码（截图经 progress 上报）→ Tier3 Cookie 导入 |
| WS | `qr_ready`（带截图 base64）→ 页面渲染二维码；`login_success` → 刷新状态 |

失败面：worker 拉不起（Chrome 缺失）→ `BrowserClosedError` toast；扫码超时 → `login_qr_timeout` → 账号仍 inactive，可重试。

### S4 验证会话

点"验证" → 复用 Tier1 逻辑 → 结果是**布尔**（有效/失效），不是"错误"。失效时提示"请重新登录"。

### S5 新建采集任务（搜数据）

选平台 → 账号下拉**只列该平台且已登录**的账号 → 填关键词（逗号分隔）→ 排序/每词上限/是否下图/是否评论 → 提交。

校验（服务端，报错要能指导修复）：
1. 平台必须在 registry 里；
2. 账号必须存在、属于该平台、状态 `active`（未登录先去登录）；
3. 关键词非空；
4. 同时只允许一个 pending/running 任务。

提交成功 → 跳到任务详情页。

### S6 任务执行（抓数据）

worker 按 platform.yaml 的 step 链回放：`search` → 去重 → `detail` →（下图）→ 节律延迟 → `comments`（top20）→ 落库。

用户在任务详情页看到：进度条（真实百分比）、已抓数、断点索引、实时日志流、取消按钮。

| 层 | 目标行为 |
|---|---|
| worker | 每笔记 progress 带 `percent/posts_scraped/last_note_index` |
| WS | tracker 转发 progress → 页面更新进度条与日志 |
| 降级 | WS 断开时页面轮询 `GET /api/tasks/{id}/progress` |
| 节律 | 关键词间 60–180s、笔记间 30–90s；22:00–07:00 直接 QuietHoursError；日限 200 |

### S7 看数据

`/posts` 列表（平台/关键词筛选 + 分页）→ `/posts/{id}` 详情（正文 + 图 + 评论按 rank）。

### S8 导出

任务详情或素材页 → `GET /api/export?task_id=&format=ai|excel` → 下载 CSV / ZIP。

### S9 异常与恢复

| 场景 | 目标行为 |
|---|---|
| 关掉 Chrome | tracker 探到 worker 死 → `BrowserClosedError` + 任务置 failed → 点"继续任务" 从断点续（已抓的跳过） |
| 验证码 | 任务 `paused` + `captcha_required` → 人工在 Chrome 里过验证 → "继续任务" |
| 安静时段 | 建任务即报 QuietHoursError，说明可用时间窗 |
| 日限满 | DailyLimitError，说明明天再来 |
| 关闭 web | 所有 worker 一并停（不留孤儿 Chrome） |

---

## 2. 第二轮 Review：SOP 撞到的缺口（G1–G18）

状态：⬜ 未开始　🔄 进行中　✅ 完成

| # | 级别 | SOP 步 | 缺口（用户可感知的现象） | 裁决/修法 | 状态 |
|---|---|---|---|---|---|
| G1 | P0 | S6 | `collect_comments=False` 时 `comments` 变量未定义 → 落库抛 NameError 被宽 except 吞掉 → **一条都存不进去**，任务却报 ok | 每条笔记初始化 `comments=[]`；落库异常只吞 DB 层异常并计入 `store_failed` 上报 | ✅ |
| G2 | P0 | S3/S4 | login/validate/import-cookies 三个路由把 `platform` 硬编码成 xiaohongshu → 非 XHS 账号登录必错平台 | 从 DB 读 `account.platform` |✅ |
| G3 | P0 | S2 | 建账号/导入 Cookie/删除都是 303 整页重定向被 HTMX 塞进 `#accounts-list` → 页面套页面 | 抽 `_accounts_table.html` 片段，三个写操作都返回片段 |✅ |
| G4 | P0 | S3 | 二维码截图经 progress 上报，但 tracker 只当普通 progress 转发 → **UI 永远看不到二维码** | tracker 建 progress→WS 事件映射（`qr_ready`/`captcha_required`/`login_success`），账号页渲染二维码 |✅ |
| G5 | P0 | S6 | `app.js` 从不派发 `ws:message`，任务详情页监听它 → 实时日志/统计永远不动 | app.js 派发 `CustomEvent('ws:message')`，页面脚本消费 |✅ |
| G6 | P1 | S6 | 任务页每 3s 轮询 `/api/tasks/{id}/progress`，该接口不存在 → 404 刷屏且降级失效 | 实现该接口（progress 文件 + DB 状态） |✅ |
| G7 | P1 | S6 | progress 里没有 percent，进度条永远 0；且 app.js 找 `progress-<id>`，模板里叫 `progress-fill` | worker 算真实百分比；统一进度条元素契约 |✅ |
| G8 | P1 | S5 | 建任务零校验：账号可以是 0/不存在/别的平台/没登录 → 一路走到 worker 才炸 | 服务端四项校验 + 400 带 fix_hint；账号下拉只列该平台 active |✅ |
| G9 | P1 | S6/S9 | `daily_scrape_count` 永不自增 → 日限 200 形同虚设；也无跨天重置 | 每落库一条自增并写 `last_scrape_at`；跨天自动归零（additive 列 `daily_count_date`） | ✅ |
| G10 | P1 | S6 | 预热用 `engine.page`，此时还是 None → 预热静默跳过（反检测 Layer 4 缺一环） | engine 暴露 `ensure_page()`；预热前先取页 | ✅ |
| G11 | P1 | S1/S9 | 没有任务列表页，历史任务/断点续跑找不到入口 | 加 `/tasks` 列表 + dashboard 真实计数与运行中入口 |✅ |
| G12 | P1 | S9 | web 退出不停 worker → 孤儿 Chrome 常驻 | app shutdown 钩子 → `supervisor.shutdown_all()` |✅ |
| G13 | P2 | S2/S5 | registry 只扫包内 `platforms/`，用户录制生成的 yaml 无处安放（设计 §19 走不通） | registry 额外扫 `data/collection/platforms/*/platform.yaml`，用户目录优先 |✅ |
| G14 | P2 | S4 | 会话失效返回 `status="error"` → UI 弹"未知错误" | 契约改 `ok + valid:false`，UI 显示"未登录/已失效" |✅ |
| G15 | P2 | S6 | `_upsert_post` 每个字段都写一遍 `getattr(...) or dict.get(...)`，40 行重复 | 边界处一次性归一化为 dict（`_as_mapping`） | ✅ |
| G16 | P2 | S6 | 搜索 flow 忽略 `sort`（URL 模板里没有 sort），评论 flow 无触发步 → 等 XHR 必超时 | XHS yaml 补 sort 参数 + 评论前 scroll 触发 |✅ |
| G17 | P2 | S9 | 安静时段写死 22–07，本机测试/白天以外无法演练；无关闭开关 | `QUIET_HOURS` 支持 `off`/环境变量覆盖（`None`=不限） | ✅ |
| G18 | P0 | 全链路 | 端到端只有 mock 引擎测试，真实链路从未被自动验证 | 建**真站点 + 真 Chrome + 真 worker** 的 E2E（`tests/e2e/`），Chrome 缺失才 skip |✅ |
| G19 | P1 | S6/S7 | 落库丢字段：`post_type/image_urls/tags/published_at` 抓到但没写进 posts → 详情页没图没标签 | 落库补全这些列（image_urls/tags 存 JSON，published_at 解析 epoch/ISO） | ✅ |
| G20 | P1 | S6 | `warmup.random_browse` 每页硬睡 30–90s 且 url 列表根本没被使用（调用参数写错）→ 预热要么假跑要么卡死任务 | 重写：真导航 url + 停留走 `rhythm.warmup_dwell()`（config 可调/可关），测试可中和 | ✅ |
| G21 | P0 | S5/S6 | `_check_rhythm` 用 `except Exception: pass` 把 `DailyLimitError` 一起吞掉 → 日限红线永久失效（有计数也不生效） | 节律异常必须外抛，只有账号查库失败才兜底 | ✅ |
| G22 | P1 | S1 | 顶部导航/引导卡指向 `/collection`，该路由不存在 → 点进去 404 | manifest 声明 `NAV`（账号/任务/素材），外壳按声明渲染真实页面 | ✅ |
| G23 | P0 | S6 | engine 直接用 yaml 里的相对路径导航，从不拼 `base_url` → 真跑第一步就 `Cannot navigate to invalid URL`（mock page 接受任何字符串所以从未暴露） | 导航前 `urljoin(spec.base_url, rendered)`，统一在一处解析 | ✅ |
| G24 | P0 | S6 | XHR 监听在 `goto` 之后才注册 → 页面加载时发出的请求全部错过，拦截永久失效（只剩 DOM/LLM 兜底） | 取页即武装监听并缓冲响应；`wait_xhr` 从缓冲取；导航时清缓冲防止上一页残留被误消费 | ✅ |
| G25 | P0 | S6 | `extract_api` 只会在响应里找列表，详情类单对象响应（`{note:{...}}`）一个字段都抽不到 | 找不到列表时按单对象抽取（map 作用于根） | ✅ |
| G26 | P1 | S6 | DOM 兜底对 JSONPath map 也照跑，产出一行全 None 的假数据入库 | 只有 `css:`/`xpath:` map 才兜底；`extract_dom` 全空时返回 `[]` | ✅ |
| G27 | P1 | S6 | 无 API key 时每条失败项都真发一次 Anthropic 请求（网络噪声 + 401） | 无 `ANTHROPIC_API_KEY` 直接跳过 LLM 兜底 | ✅ |
| G28 | P0 | S6 | `ScrapedPost.published_at` 只接受 str，真实平台给 epoch 整数 → 整个正文组校验失败被丢弃（标题/正文全丢） | schema 接受 str/int/float，落库侧统一解析 | ✅ |
| G29 | P1 | S6 | `fetch_item` 只取 `items[0]`，把 `Post.interactions` 那半（点赞/收藏/评论数）直接扔掉 | 详情 flow 的多组抽取合并成一条 ScrapedPost | ✅ |
| G30 | P1 | S9 | supervisor 用 SIGTERM 停 worker，worker 没有信号处理 → 进程直接死，`finally` 不执行，真 Chrome 变孤儿 | worker 把 SIGTERM/SIGINT 转成正常 unwind，`finally` 里终止 Chrome | ✅ |
| G31 | P0 | S3/S5/S6 | `worker_main.py` **没有 `if __name__ == "__main__"`** → supervisor 的 `python -m ... worker_main` 只是导入模块然后退出，web→worker 这条路从来没通过 | 补 `__main__` 块调用 `sys.exit(main())`；新增"模块可执行"回归测试 | ✅ |
| G32 | P0 | S3/S6 | worker 拉起 Chrome 后立刻 `connect_over_cdp` → 首次必 ECONNREFUSED，worker 当场失败 | `attach()` 内置等端口就绪重试（默认 30s） | ✅ |
| G33 | P2 | S9 | worker 日志重定向到文件后是块缓冲，被 SIGTERM 杀掉时日志尾巴全丢，事故没有现场 | supervisor 用 `python -u` + `PYTHONUNBUFFERED=1` 拉起 | ✅ |

---

## 3. 真实测试策略（不用 mock 引擎）

三层，全部真实组件：

1. **真实站点**：`tests/e2e/local_site.py` 用 stdlib `http.server` 起一个真 HTTP 服务，
   提供搜索页/详情页/登录页（真 HTML + 真 `fetch()` XHR）与对应 JSON API。
   它扮演"一个我们要抓的平台"，因此 platform.yaml 的 step 链、XHR 拦截、JSONPath 抽取全是真跑。
2. **真实平台注册**：把 `localtest` 的 platform.yaml 写进用户平台目录（G13 的通路），
   由 registry 真实加载 —— 顺带验证"用户自己录制的站点能被系统发现"。
3. **真实浏览器 + 真实 worker**：`launch_real_chrome`（仅 `--remote-debugging-port` + `--user-data-dir`）
   + `connect_over_cdp`，跑真 `handler_login` / `handler_scrape_task`，断言 SQLite 里真有 posts/comments，
   再用真 `TestClient` 走 `/posts`、`/api/export` 验证读侧。

约定：真实 E2E 只在 `config.CHROME_BIN` 存在时运行（macOS 开发机=运行，CI 无 Chrome=skip），
不使用 mock engine / mock page 替身；单元测试可以用真实的假站点数据，但不允许用 mock 掩盖未实现的行为。
