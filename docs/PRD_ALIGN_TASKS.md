# PRD_ALIGN_TASKS — Skim 采集模块 PRD 对齐任务切分

> **单一进度真相源**（与 PRD 对齐迭代）。每次会话查本表选一个 ⬜ 会话（S），做完跑门禁、更新本表状态、commit+push。  
> **两层粒度**：上层 **会话 S1-S9**（每个=一个有界会话上下文，内聚一组改动）；下层 **T 子任务**（会话内 checklist，不必每 T 开一个会话）。  
> 设计裁决依据：`docs/semilabs_hone_skim_sepc.md`（PRD）+ `~/.claude/plans/semilabs-hone-docs-semilabs-hone-skim-s-snoopy-swan.md`（已批准）。  
> 取舍总纲：**PRD 安全/正确性红线全盘接受并推翻已实现违规代码；架构层（WS 推送、recorder+LLM 通用引擎）保留只补 PRD 行为；验证码自动解作为可选能力沉淀（默认关）**。

---

## ⛓️ 共享上下文契约（每会话开工先读，不可擅自漂移）

> 这些是跨会话的**不可变契约**。任何会话若需修改，必须回写本节并标注 `[契约变更]`——这是跨会话决策，不是单会话可定。  
> 每个会话开工第一步：读本节 + 自己的 S 段 + 目标文件，**不需要**重读全部历史。

**[契约变更 2026-07-10]**
- 夜间休眠窗口 22:00-07:00 → **02:00-08:00**（取 PRD §2.2 场景；§4.5.1 与之矛盾，以场景为准）。已同步 config/rhythm/测试。
- IPC `IPCResult.status` 新增 `need_human` 字面量（原映射 paused+current_stage 改为显式枚举值）。
- 数据表采用**原地改表**（无 Alembic、factory.db 被 gitignore、无生产数据 → create_all 重建，dev 数据可丢），删除 T16 搬迁脚本。
- control 指令 JSON 统一 `{"action":"pause"|"resume"|"stop"}`。

**1. 状态枚举（DB `status` + IPC `status`，全厂统一）**
- 任务 DB status：`pending | running | need_human | paused | completed | error`
- IPC result status：`ok | error | paused | cancelled | need_human`（命中验证码/登录失效直接 `need_human`，UI 据此红灯）
- 瞬态（仅 IPC `progress/`，不入 DB）：`fetching_list / reading_content / resting / night_sleep / captcha_detected / login_expired`

**2. 数据表契约（PRD §6，原地改表：无 Alembic、factory.db 被 gitignore、无生产数据，create_all 重建即可）**
- `collection_tasks`：id(UUID str36) PK · platform · task_type(`keyword_search`/`author_homepage`) · target_value · status · expected_count · actual_count · error_msg · created_at · updated_at
- `collection_items`：id PK · task_id FK · platform · platform_id · url · title · content_text · author_name · metrics_json(TEXT,`{"likes","comments_count",...}`) · publish_time(VARCHAR 容错) · scraped_at · UNIQUE(platform,platform_id)
- `collection_comments`：id PK · item_id FK · platform_comment_id · author_name · content_text · like_count · scraped_at · UNIQUE(item_id,platform_comment_id)
- upsert：`INSERT ... ON CONFLICT(...) DO UPDATE`（断点续传+去重幂等）
- 引擎：WAL + `check_same_thread=False` + `timeout=15`
- 旧 `scrape_tasks/posts/comments` 模型直接改名/改字段（不保留双表、不写搬迁脚本）

**[契约变更 2026-07-10 S3 过渡态]**（实测消费者耦合后的最小逻辑改动裁决）
- S3 已上 PRD §6 全部新列 + UUID str36 PK + CASCADE FK(item_id/task_id) + UNIQUE 新名(uix_platform_item/uix_item_comment) + WAL + repository.upsert；类名 `ScrapeTask/Post/Comment→CollectionTask/CollectionItem/CollectionComment`，文件 `post.py/comment.py` 类改名（文件名保留以零 import 改动）。
- **超表过渡**：为不破坏 S4/S7 拥有的 `handlers._upsert_post`/`csv_exporter`（仍读写旧列 `content/likes/collects/comments_count/shares/tags/post_type/image_count/post_id/platform_id(评论)/sub_comment_count/is_author_liked/rank/published_at/max_posts_per_keyword/posts_scraped/last_note_index/sort_type/download_images/collect_comments/error_message/error_category/account_id/keyword_id/...`）与 `test_csv_export`/`test_integration` 的 int-id seeding，S3 **保留**这些旧列并保留旧 `UNIQUE(post_id,platform_id)`，与 PRD 新列并存。旧列 FK 已移除（account_id/keyword_id/post_id 仅裸列）。
- **`metrics_json` 闲置**：S3 只建列+repository 写它；handlers 仍写旧 `likes` 等。S4 改 handlers 走 `repository.upsert_item`(content_text/metrics_json) 时切聚合；S7 改 csv_exporter 读 `content_text/metrics_json` 并落宽表；届时从模型删旧列 + 旧 UNIQUE（create_all 重建即生效）。
- **`task_id` 类型**：全链 int→str(UUID)。`routes/handlers/csv_exporter` 的 `task_id` 参数注解 int→str、`test_csv_export`/`test_integration` 显式 int id seeding→str——均机械，零逻辑改动。watchdog 仅类名替换（status/error_message/error_category 旧列保留）。
- **PRD NOT NULL 暂缓**：`collection_items.url`、`collection_comments.platform_comment_id` PRD 标 NOT NULL，但旧 seeding/handlers 不填；S3 暂设 nullable，S4/S7 切换后改回 NOT NULL。
- **routes 创建表单**：`routes/tasks.py` 仍收旧 form(account_id/keywords/sort/max_posts/download_images/collect_comments) 并派生填 PRD 列(task_type="keyword_search"/target_value=kw[0]/expected_count=max_posts)，IPC payload 向后兼容。完整 dialog 迁移属 S6/T32。
- **清理责任**：S4 删 handlers 旧列引用+切 metrics_json；S6 删 routes 旧 form/TaskKeyword/Keyword 链；S7 删 csv_exporter 旧列读+落宽表+删旧 UNIQUE。各 session 清理后从模型删对应旧列。

**[契约变更 2026-07-11 S6]**（UI 行为对齐，加列非删列）
- `collection_tasks` 新增 `request_id String(12) nullable`（additive，create_all 重建，dev 数据可丢）。用于 T31 状态徽章关联 `progress/<rid>.json` 取 night_sleep/resting 瞬态，并为 S9 的 resume→`control/ctrl_<rid>.json` wiring（PRD §4.4.3）留接口。`api_create_task` 生成 request_id 后回写 `task.request_id`（T32 顺手）。
- T32 form 迁 PRD §4.1.1 模型（`task_type/target_value/expected_count`），删 `api_create_task` 里 TaskKeyword/Keyword 建链段；**不 drop** Keyword 模型（`posts.py /posts` 筛选仍读 `Keyword.text` + `CollectionItem.keyword_id` 旧列，删模型会崩筛选）。IPC payload 从 target_value 派生 `keywords`/`target_urls`，**S4 引擎零改动**（向后兼容）。
- T34 master-detail 落 `posts.html`（列表行展开）非任务文本写的 `post_detail.html`——后者是单篇内联评论，行展开无意义；PRD §5.4.2 master-detail 是列表行模式。
- resume→control 文件 wiring 仍留 S9/T70（需 worker 当前 request_id 追踪，属端到验 wiring）；S6 只做 UI 乐观锁 + need_human 按钮视觉。`task.request_id` 已存，S9 接 `control/ctrl_<rid>.json {action:resume}` 即可。
- PRD §5 前言禁 WS，但契约 §7/§8 已裁决保留 WS——S6 循此：WS 留作流式进度（app.js），HTMX 负责徽章/dialog/master-detail/心跳/Toast（新端点 `GET /api/tasks/{id}/status`、`GET /api/items/{id}/comments`、`GET /api/heartbeat`）。

**[契约变更 2026-07-11 Sz] 加站扩展性铁律（知乎暂 hold，但扩展点须保通用）**
- 知乎相关工作（T27 录制、L08 data[] 兜底、L09 maps、知乎 comments scroll_collect）**暂 hold**，转 Sz 知乎专项会话统一排期，不挤占 S7-S9 主线 loop。
- **扩展点须保持通用，禁止 XHS / 知乎 special-case 分支**。加站路径只走通用 extension 点：`platform.yaml`（flow + maps + risk_tier/captcha_policy）+ `registry`（yaml 加载发现）+ `field_extract`（通用清洗，含 list-root 兜底须泛化）+ `recorder + LLM mapper`（录制三 flow → 生成 yaml）。任何「知乎特殊」需求必须**先泛化到通用层**再落地（例：L08 的 `{"data":[...]}` 不命中 `_find_list_root`，修法是让 `_find_list_root` 支持任意 list 值的 data key，而非加 `if platform == 'zhihu'` 分支）。
- engine / handlers / risk_probes / routes 不得出现平台名硬编码；平台差异 100% 收进 yaml。新增 `if platform ==` 类分支视为违例，约束 linter 后续可加守。
- Sz 启动前置：XHS（首站）端到端走通（S9/T70 验证 XHS 全链路绿），再开知乎录制，避免在未稳态上加第二站引入回退风险。

**[契约变更 2026-07-11 S8]**（BDD 7.1 日限额 wiring 修复 + 测试门禁落地）
- `_check_rhythm(account_id, progress_cb, now=None)` 改为查 SQLite 当日 `collection_items` 总数（跨任务累加，匹配 PRD §7.1「当天总入库量」），用 `{"daily_scrape_count": today_count}` 调 `check_daily_limit`（复用已测函数）。`DailyLimitError` **不再被 `except Exception: pass` 吞掉**——让它穿透。
- per-note 循环内（`_night_sleep_if_quiet` 之后）调用 `_check_rhythm`，使「第 50 条命中 200」语义成立（不只起步查一次）。handler 捕获 `DailyLimitError` → 置 task `paused`（区别于 captcha 的 `need_human`，PRD §7.1 明令 paused）+ `progress_cb("daily_limit", {msg:"全局日配额已达上限，保护机制生效，请明日恢复"})` + break + `return {"status":"paused","reason":"daily_limit"}`。新增 `_set_task_paused` helper。
- 裁决记 WHY：PRD §7.1 文案是「全局日限额跨任务累加 / 当天总入库量达到 200」→ 用全局 COUNT 当日入库，非 per-account；`config.DAILY_LIMIT_PER_ACCOUNT` 变量名是存量误名但值 200 正确，**不改名**（additive，不触碰既有语义）。pre-S8 的 `daily_scrape_count` 列从未被任何代码自增 → 日限额双重失效，本修复用 SQLite COUNT 绕开该列（列保留，零 schema 改动）。
- **覆盖口径**：全包 85%，分母 = `semilabs_hone` 全量（含 hold/可选模块 recorder/slide_solver/ua_pool/ocr/manual_handler，均 mock 驱动）。`loop_gate.sh` 接 `--cov=semilabs_hone --cov-fail-under=85`；`pyproject.toml` 加 `pytest-cov` 到 dev deps + `[tool.coverage.report] fail_under=85`。当前 86%（8511→3511 语句，505 缺失）。
- **遗留 L12**：`routes/tasks.py api_resume_task` 在 `sess.close()`（finally）后访问 `task.account_id/platform/...`（commit 触发 expire_on_commit）→ DetachedInstanceError。BDD 7.1 happy-path resume 测试因此跳过（保留 409 冲突 + 404 缺失分支测试）。归 S9/T70 或后续修复（payload 构造移入 try 内 close 前）。

**3. IPC 契约（PRD §3.4/§7.2）**
- 目录 `data/ipc/{requests,results,progress,control}`（control 扁平，无 cancel 子层；`control/cancel/` 仅旧哨兵兼容）
- 命名：`requests/<id>.json` · `results/<id>.json` · `progress/<id>.json` · `progress/heartbeat.json` · `control/ctrl_<id>.json`
- 读后即焚：request/control 文件加载入内存后**立即 `burn()`**；坏 JSON catch+burn 不崩（PRD §8.3 场景3.1）
- 心跳：worker 每 10s 写 `progress/heartbeat.json`；web 30s 过期→task running→paused + WS 广播
- 单任务并发锁：同时仅 1 个 `running`（MVP 单浏览器）

**4. 反检测红线（PRD §7.1，约束 linter 守）**
- ❌ 零脚本注入（stealth/Canvas/Audio 全 no-op）；✅ 真实 Chrome UA（CDP 读取）
- ❌ `page.evaluate(scrollBy)` 瞬移；✅ `mouse.wheel` 多步+微停顿
- ❌ `element.click()` 正中心；✅ `bounding_box`+随机偏移±10px+`mouse.click`
- ❌ 裸 `time.sleep(N)`；✅ `wait_for_selector`+`random.uniform(1.5,3.5)`
- ❌ `while True`/`while is_captcha:` 死循环重试刷新
- ✅ 夜间 **02:00-08:00** 长 `asyncio.sleep` 至 08:00，零网络请求

**5. 验证码可选能力契约（新增裁决）**
- `platform.yaml` 字段：`risk_tier: account|anonymous`（默认 account）· `captcha_policy: manual|auto_then_manual`（默认 manual）
- 默认 manual：命中即 `need_human` 立即转人工（XHS/知乎）
- 仅 `anonymous + auto_then_manual`（cargo 类无登录站点）才走 slide/ocr，失败 1 次转人工、不死循环
- 全局默认：`config.CAPTCHA_DEFAULT_POLICY="manual"`，`CAPTCHA_AUTO_SOLVE_PLATFORMS=[]`

**6. CSV 交付契约（PRD §4.6）**
- 左连接宽表：一笔记 N 评论→N 行；0 评论→1 行评论列空
- 10 列中文表头：平台/笔记ID/笔记标题/笔记正文/笔记点赞数/笔记发布时间/笔记链接/评论者昵称/评论内容/评论点赞数
- `utf-8-sig` BOM；`csv.DictWriter` 正确转义 emoji/逗号/引号；0 条→拦截+Toast

**7. UI 契约（保留 WS 推送 + 补 HTMX 局部交互）**
- WS 负责流式进度（worker→progress 文件→web 代广播）；HTMX 负责徽章/dialog/master-detail/Toast 局部替换
- 全程无整页 reload；错误走 Toast/内联红字

**8. 架构保留裁决（不推翻）**
- 保留 `recorder+llm_mapper+GenericEngine` 通用引擎 + `platform.yaml`（比硬编码更强，XHS yaml 已成型）
- 保留 FastAPI + WebSocket 外壳
- 加站 = 录制三 flow + LLM 生成 yaml（知乎首站）；非硬编码 adapter

---

## 🧭 会话经验（防错原则）— 每会话读契约后扫一眼

> 本会话踩出来的、契约里没写的经验。固化下来免得下个会话重复交学费。

1. **契约值变更会回溯打脸"已完成"会话**。改 config/契约里的值（如 `QUIET_HOURS`、status 枚举）前，**grep 它在代码里的语义假设**，不只看值。例：`(22,7)→(2,8)` 暴露了 `is_quiet_hours` 的 OR-only 逻辑只认跨午夜窗口，对同日窗口判成全天静默。改值=立即触发第 2 条。
2. **绿灯测试可能编码的是旧/被禁行为**。推翻一个设计决策后，**主动找出断言旧行为的测试并改写**——别把"契约变更后的红"当回归去"修回旧行为"。例：`scrollBy→mouse.wheel` 后 `test_random_scroll` 仍断言 `evaluate`；`22→02-08` 后整组 `quiet_hours` 测试断言旧窗口；`4→5 字面量` 后旧测试断言 4 个。
3. **PRD 自相矛盾时显式裁决**。本 PRD 至少三处自相矛盾（夜间窗口 §4.5.1 `22-07` vs §2.2 `02-08`；control 键 §4.4.3 `action` vs §3.4 `cmd`；feed GET vs POST，§9.1 已勘误为 POST）。遇到就**标出来→问用户→把"哪条胜出+为什么"写进契约变更日志**，别默默选一个，否则下个会话会再撞一次。
4. **裁决记 WHY 不只记 WHAT**。每条"保留/推翻"都附一句理由。例："保留 WS——流式进度推送本质更适合，推翻是纯重写零新能力"。否则下个会话看到"保留 WS"会以为是不小心留下的，"好心"迁移成 HTMX。
5. **会话粒度=文件/测试内聚，非原子性**。共享同一批文件+同一测试模块的子任务**并成一个会话**（如 T11/T12/T13 三表都动 `models/db.py`+`test_models.py`）。避免反复重读同批文件、上下文浪费。
6. **代码 vs 规格评审用只读 Explore 并行扇出**。按子系统派 2-3 个 Explore agent 各审一块，比串行读 10k 行高效得多；只取结论不取文件 dump。本会话靠它一次定位 8 处冲突 + 6 处缺口。
7. **依赖时间/节拍的逻辑必须暴露注入点，测试不靠墙钟**。全项目节奏/睡眠密集（节律 NOTE_DELAY/夜间 sleep/心跳 10s/看门狗阈值 30s）。任何 `time.time()`/固定间隔要么作参数（`now=`）传入、要么作模块常量（`HEARTBEAT_INTERVAL`），测试用 `monkeypatch`/传参注入小值；绝不在被测逻辑里写死墙钟，否则节律类测试只能真实等待几十秒、且与时序竞态 flaky。S1 rhythm 已循此式（`is_quiet_hours`/`sleep_until_wakeup` 显式传时间），S4 接入主循环时须保持。

---

## 状态图例

⬜ 未开始　🔄 进行中/代码完成待人工验　✅ 完成　⛔ 阻塞　⏸ hold（暂缓，等专项排期）

## 门禁定义（Definition of Done）

每个**会话 S** done = 机器可判 0/1：
1. **约束 linter**：`python3 scripts/check_constraints.py` 退出 0。
2. **会话门**：该 S 段列出的 pytest 目标退出 0。
3. **全量回归**：`bash scripts/loop_gate.sh` 退出 0（约束 + 全量 pytest，不破坏已✅会话）。
4. **可自动**：✅=loop 全程自动；🟡=loop 写代码+单测、done 需人工签；❌=纯人工 loop 跳过。

**熔断**：单会话 3 次不过门 → ⛔ 停、报根因、等介入（CLAUDE.md §错误熔断）。

---

## 会话级编排（S1-S9 + S6b = 有界会话）

> 每个 S = 一个会话上下文。内含 T 子任务作为会话内 checklist（勾完即 done）。下一会话只接一个 S。

| 会话 | 状态 | 可自动 | 依赖 | 主题 | 范围 | 会话门（pytest） |
|---|:-:|:-:|---|---|---|---|
| S1 | ✅ | ✅ | — | P0 反检测+节律+IPC 原语 | T01-T04 | test_human_behavior/test_rhythm/test_fingerprint/test_ipc |
| S2 | ✅ | ✅ | S1 | P0 IPC/安全 wiring | T05 server 读后即焚+坏文件+心跳+control 分发 · T06 心跳看门狗 30s→paused · T07 单任务并发锁 · T08 cdp 端口冲突 · T09 约束 linter 扩展 | test_ipc/test_routes/test_cdp/check_constraints |
| S3 | ✅ | ✅ | S2 | P1 数据模型对齐 | T10 WAL · T11-T13 collection_* 三表原地改名(UUID+UNIQUE+metrics_json+publish_time VARCHAR) · T14 repository upsert · T15 schemas 校验 | test_models/test_routes |
| S4 | ✅ | ✅ | S3 | P2 引擎+探针+节律 | T20 单条跳过+计数 · T21 go_back+滚动边界20/5+删 scrollBy · T22 parse_likes/title_fallback · T23 评论 Top20 · T24 风控探针 · T25 节律暖场接入主循环 | test_engine/test_field_extract/test_rhythm + 新 test_risk_probes |
| S5 | 🔄 | 🟡 | S4 | P2 可选验证码 + 知乎录制 | T26 ✅ solver 风险分层(risk_tier/captcha_policy 默认 manual) · T27 ⏸ 知乎 platform.yaml 骨架(hold，转 Sz 专项) | 新 test_solver + 人验 zhihu |
| S6 | ✅ | ✅ | S3,S2 | P3 UI 行为（保留 WS） | T30 htmx+Pico · T31 状态徽章 · T32 创建任务 dialog+校验+耗时 modal · T33 乐观锁 · T34 master-detail 评论 · T35 心跳指示灯 · T36 错误 Toast | test_routes |
| S6b | ✅ | ✅ | S6 | P3.5 任务控制台列表页 | T37 GET /tasks 列表页+空状态 · T38 行/操作片段端点(乐观刷新) · T39 创建→afterbegin 插行接入 | test_routes |
| S7 | ✅ | ✅ | S3 | P4 CSV 宽表 | T40 宽表重写(10 中文表头+utf-8-sig+转义) · T41 导出路由+空数据防御 | test_csv_export/test_routes |
| S8 | ✅ | ✅ | S4-S7 | P5 测试门禁 | T50 PRD §8 全部 BDD 落 pytest · T51 覆盖率 ≥85% | tests/prd_bdd/ + cov 门 |
| S9a | ⬜ | 🟡 | S2-S8 | P7.5 端到端 wiring 修复（T70 前置） | L13 web→worker Popen · L14 ctx 注入 engine · L15 login QR 真实化 · L16 WS progress relay · L01 resume→control · L10 solver wiring · L12 resume post-close | 全量回归 + 人验启 Chrome |
| S9 | ⬜ | 🟡/❌ | S9a | P6 文档同步 + P7 端到验 | T60-T63 spec/design/context 自洽 · T70 ❌ 端到端 · T71 ❌ 验证码可选能力验证 | 文档自洽 + 人工 |
| Sz | ⏸ | 🟡/❌ | S4,S9 | 知乎专项定制（暂 hold） | 真实录制知乎 search/detail/comments 三 flow + LLM 生成 maps · 补 `_find_list_root` data[] 兜底 · comments scroll_collect · solver wiring 专项 | 人验 + 文档自洽 |

**loop 顺序**：S1 → S2 → S3 → S4 → S5(代码半,录制🟡) → S6 → S6b(列表页) → S7 → S8 → S9a(wiring 修复) → S9(文档+人验)。Sz 知乎专项暂 hold（不在主线 loop，等专项启动再排期）。
**跨会话并发安全**：同一时刻只推进一个 S（共享同一批文件+DB+IPC 契约，并行会冲突）。

---

## P0 — 安全红线与正确性修复（最高优先）

> 目标：消除封号风险源 + 卡死 bug。不动表结构。S1 已完成 T01-T04，S2 待开。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T01 stealth 零注入 | ✅ | ✅ | — | anti_detect/stealth.py 降级 no-op | test_fingerprint+test_contract_collection |
| T02 拟人滚动/点击/等待 | ✅ | ✅ | T01 | human_behavior.py mouse.wheel + smart_wait | test_human_behavior |
| T03 夜间长 sleep | ✅ | ✅ | — | rhythm.py is_quiet_hours+sleep_until_wakeup | test_rhythm |
| T04 IPC 原语 | ✅ | ✅ | — | paths.py control_dir/burn/heartbeat/bad-JSON | test_ipc |
| T05 IPC server 读后即焚+坏文件+心跳 | ✅ | ✅ | T04 | server.py: 请求加载后立即 burn；坏 JSON catch+burn；serve 循环每 10s write_heartbeat；control 文件读后即焚分发 pause/resume/stop | test_ipc(新增 server 读后即焚+坏文件+心跳用例) |
| T06 心跳看门狗 | ✅ | ✅ | T04,T05 | client.py poll_heartbeat；web 侧 30s 过期→DB task running→paused + WS 广播「引擎异常中断」 | test_ipc(看门狗) + test_routes |
| T07 单任务并发锁 | ✅ | ✅ | — | routes/tasks.py 建/启动前查 status=running 计数>0 拒绝排队（PRD 8.2 场景2.2） | test_routes |
| T08 cdp 端口冲突 | ✅ | ✅ | — | browser/cdp.py 9333-9340 探测递增；CDP 连接失败→paused+UI 提示关 Chrome（PRD 8.1 场景1.2） | test_cdp |
| T09 约束 linter 扩展 | ✅ | ✅ | T01 | check_constraints.py 禁 while True/is_captcha 死循环；禁 account 站点 captcha_policy=auto_then_manual | check_constraints.py |

## P1 — 数据模型对齐（PRD §6）

> 契约变更：原地改表（无 Alembic、factory.db 被 gitignore、无生产数据），create_all 重建，dev 数据可丢。旧模型直接改名/改字段，不写搬迁脚本。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T10 DB 引擎 WAL | ✅ | ✅ | — | models/db.py check_same_thread=False+timeout=15+PRAGMA journal_mode=WAL | test_models |
| T11 collection_tasks 表 | ✅ | ✅ | T10 | models/task.py CollectionTask(UUID PK, task_type/target_value/expected/actual/error_msg/updated_at) | test_models |
| T12 collection_items 表 | ✅ | ✅ | T11 | models/post.py→collection_item.py metrics_json TEXT+publish_time VARCHAR+UNIQUE(platform,platform_id) | test_models |
| T13 collection_comments 表 | ✅ | ✅ | T12 | models/comment.py→collection_comment.py UNIQUE(item_id,platform_comment_id) | test_models |
| T14 repository upsert | ✅ | ✅ | T12,T13 | models/repository.py ON CONFLICT DO UPDATE upsert_item/upsert_comment | test_models |
| T15 schemas 校验 | ✅ | ✅ | T11 | schemas.py TaskCreate 校验 http 前缀+count[1,200]截断 | test_routes |
| ~~T16 数据搬迁脚本~~ | — | — | — | 契约变更删除（原地改表，无需搬迁） | — |

## P2 — 采集能力补全（PRD §4.2-4.5）

> 保留通用引擎。代码 ✅ / 真录制 🟡。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T20 engine 单条跳过+计数 | ⬜ | ✅ | T14 | engine.py 单条 try-except→记日志+actual_count+1+continue（PRD 8.4 场景4.1） | test_engine |
| T21 engine go_back+滚动边界 | ⬜ | ✅ | T20 | engine.py 详情页后 go_back；滚动硬上限20/连续5空跳出（PRD 8.4 场景4.2）；删 engine 内 scrollBy 改 mouse.wheel | test_engine |
| T22 field_extract 清洗 | ⬜ | ✅ | — | field_extract.py parse_likes("1.2w"/"1.5万"/"赞"→0)+title_fallback(content[:20]) | test_field_extract |
| T23 评论 Top20 | ⬜ | ✅ | T14,T22 | handlers/engine 按 likes 降序截前20，不足全收；评论区最多3次滚动加载 | test_engine |
| T24 风控探针 | ⬜ | ✅ | T05 | 新 risk_probes.py：goto/scroll/click 后探测(XHS captcha class/知乎signin重定向/扫码QR)；命中→break+need_human+IPC广播+2s 轮询 control/resume 先重跑探针 | test_risk_probes(新) |
| T25 节律暖场接入 | ⬜ | ✅ | T02,T03 | engine/handlers goto 后 random.uniform(30,90)暖场；微操1.5-3.5；列表滑动5-10；主循环 is_quiet_hours→sleep_until_wakeup | test_engine+test_rhythm |
| T26 可选验证码能力 | ✅ | ✅ | T24 | captcha/solver.py detect_and_solve 加 risk_tier/captcha_policy 参数，默认 manual→立即 need_human；anonymous+auto_then_manual 才走 slide/ocr 失败1次转人工；platform.yaml 新增 risk_tier/captcha_policy 字段 | test_solver(新) |
| T27 知乎适配器 | 🔄 | 🟡 | T22-T25 | 录制 search/detail/comments 三 flow 生成 platforms/zhihu/platform.yaml（人工录制） | registry 加载+人验 |

## P3 — UI 行为对齐（PRD §5，保留 WS）

> 不重写传输层；WS 流式进度 + HTMX 局部交互并存。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T30 HTMX+Pico 引入 | ✅ | ✅ | — | base.html 加 htmx.js + Pico CSS；保留 app.js(WS) | test_routes(渲染 200) |
| T31 状态徽章 | ✅ | ✅ | T30 | task_detail.html need_human 红闪烁/night_sleep 深色+07:00文案/pending/running/paused/completed/error | test_routes |
| T32 创建任务 dialog | ✅ | ✅ | T15,T30 | task_new.html 改 dialog+失焦校验(http/count≤200)+耗时预估二次确认 modal+aria-busy | test_routes |
| T33 操作按钮乐观锁 | ✅ | ✅ | T07,T30 | hx-post 后 aria-busy+disabled；need_human 高亮唤起/已处理继续 | test_routes |
| T34 master-detail 评论 | ✅ | ✅ | T13,T30 | post_detail.html 行点击 hx-get=/api/items/<id>/comments hx-swap=afterend；无评论置灰 | test_routes |
| T35 全局心跳指示灯 | ✅ | ✅ | T06,T30 | base.html 导航栏底部轮询 heartbeat 绿/红灰+「引擎离线」 | test_routes |
| T36 全局错误 Toast | ✅ | ✅ | T30 | app.js/inline 监听 htmx:responseError/sendError→右上红 Toast 3s | test_routes |

## P3.5 — 任务控制台列表页（PRD §5.2，S6 遗留补全）

> S6 裁决「不顺带补 /tasks 列表页」；本段把该缺口正式立项。复用 S6 已建的
> `GET /api/tasks/{id}/status` 徽章端点（列表行 `<td id="status-<id>>` 直接轮询它），
> 故依赖 S6✅。全程 HTMX 局部替换，无整页 reload。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T37 任务控制台列表页 | ✅ | ✅ | S6 | 新 `GET /tasks` 路由 + `tasks_list.html`（表头: 任务ID/平台/类型/目标值/进度 actual/expected/状态徽章/操作）；`<td id="status-<id}">` HTMX 5s 轮询 `/api/tasks/{id}/status`；行点击→`/tasks/{id}`；空状态居中卡片「暂无采集任务，点击右上角开始你的第一个数字分身任务吧」+【新建任务】按钮 | test_routes |
| T38 行/操作片段端点 | ✅ | ✅ | T37,S6 | 新 `GET /api/tasks/{id}/row` 返回整 `<tr>` 片段（含 status-/actions- id，供创建后 afterbegin 插入）；新 `GET /api/tasks/{id}/actions` 返回操作 `<td>` 内片（cancel/resume/唤起/已处理 按 status，供乐观锁请求后局部刷新 actions 单元格） | test_routes |
| T39 创建→列表接入 | ✅ | ✅ | T38,T32 | `task_new.html` 成功后：若在 /tasks 页→`hx-swap="afterbegin"` 插新行（GET /api/tasks/{id}/row）+ 绿 Toast「任务已就绪」(PRD §5.3.2)；不在列表页则维持现有「查看详情」链接行为 | test_routes |

## P4 — CSV 宽表交付（PRD §4.6）

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T40 CSV 宽表重写 | ✅ | ✅ | T12,T13 | csv_exporter.py 左连接宽表(一行一评论/0评论1行)+10中文表头+utf-8-sig+csv转义emoji/逗号/引号 | test_csv_export |
| T41 导出路由+空数据防御 | ✅ | ✅ | T40 | routes/export.py 0条→400 JSON+前端 Toast；按钮改 fetch 下载 | test_routes+test_csv_export |

## P5 — 测试与约束门禁

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T50 PRD 第8章 BDD | ✅ | ✅ | 各任务 | 把 PRD §8 全部 Given-When-Then 落 pytest(1.1/1.2/2.1/2.2/3.1/3.2/4.1/4.2/5.1/5.2/6.1/6.2/7.1/7.2/8.1/8.2) | tests/prd_bdd/ |
| T51 覆盖率≥85% | ✅ | ✅ | 各任务 | pytest --cov=semilabs_hone --cov-fail-under=85 | cov 门 |

## P6 — 文档同步（设计与实现统一）

| 任务 | 状态 | 可自动 | 依赖 | 范围 | 门禁 |
|---|:-:|:-:|---|---|---|
| T60 PRD spec 补可选验证码 | ⬜ | ✅ | T26 | semilabs_hone_skim_sepec.md 补验证码可选能力+裁决(WS/通用引擎/表名) | 文档自洽 |
| T61 skim_design 对齐 | ⬜ | ✅ | 各P | skim_design.md §4.4零注入/§10风险分层/§5.2 mouse.wheel/§6读后即焚+心跳/§7 collection_*/§12长sleep/§14宽表 | 文档自洽 |
| T62 PROJECT_CONTEXT | ⬜ | ✅ | T09 | PROJECT_CONTEXT.md §3硬约束+§4裁决表更新 | 文档自洽 |
| T63 DEV_PLAN 接入 | ✅ | ✅ | — | DEV_PLAN.md 指向本表 + 全局里程碑 P0-P5 | 本表存在 |

## P7 — 端到端验证（❌ 纯人工）

| 任务 | 状态 | 可自动 | 依赖 | 范围 |
|---|:-:|:-:|---|---|
| T70 端到端 | ⬜ | ❌ | P0-P4 | serve→建任务→WS进度→need_human红灯→唤起Chrome扫码→resume断点续→导出中文宽表CSV(Excel不乱码) |
| T71 验证码可选能力验证 | ⬜ | ❌ | T26 | 默认 manual 立即转人工；cargo 站 anonymous+auto_then_manual 时 slide/ocr 自动解失败1次转人工不死循环 |

---

## 依赖 DAG（会话级，主链顺序）

```
S1 → S2 → S3 → S4 → S5(🟡) → S6 → S6b(列表页) → S7 → S8 → S9(🟡/❌)
        \____ S3 → S6（UI 依赖表）; S6 → S6b（复用徽章端点）; S3 → S7（CSV 依赖表）; S2 → S6（心跳指示灯）
        \____ S4,S9 → Sz（知乎专项，⏸ hold，不在主线 loop）
```

> **不并行**：MVP 单浏览器 + 共享同一批文件/DB/IPC 契约，同一时刻只推进一个 S。  
> 任务级细 DAG（T 子任务内部依赖）见各 P 段表格的「依赖」列，仅作会话内排序参考。

## 🧯 遗留事项跟踪（跨会话 open 项，单一收口表）

> 凡某会话裁决「暂不做 / 留后续」的项，**必须登记到本表**（勿只在会话快照里提一句，快照会沉底）。每项给 ID、来源、描述、收口会话/任务、状态。收口时把状态改 ✅ 并附 commit/证据。新会话开工先扫本表，看自己是否要顺手收一项。

| ID | 来源 | 描述 | 收口 | 状态 |
|---|---|---|---|:-:|
| L01 | S4/S6 | `routes/tasks.py /api/tasks/{id}/resume` 仍发新 IPC request（op scrape_task, resume=True），**未**按 PRD §4.4.3 step4 写 `control/ctrl_<rid>.json {action:resume}`。handler `_await_resume` 轮询 control/ 行为正确但真实 /resume 路径未对接。`task.request_id` 已存（S6 加列），S9 接 `control/ctrl_<rid>.json` 即可。 | S9/T70 | ⬜ |
| L02 | S6/S6b | JS 运行时行为端到验：dialog 失焦校验 / 耗时预估 modal / 乐观锁（hx-disabled-elt+lockBtn）/ master-detail toggle / Toast 触发（htmx:responseError/sendError）/ 创建后 afterbegin 插行 / 列表 actions 5s 轮询刷新。pytest 只断言静态接入（渲染含 `<dialog>`/`hx-*`/app.js 含监听串），真实浏览器行为未驱动。 | S9/T70 | ⬜ |
| L03 | S4/S6 | model 旧列 + NOT NULL 清理（契约 §2）：`collection_items.url`、`collection_comments.platform_comment_id` 改回 NOT NULL；删旧列（likes/content/post_id/rank/sub_comment_count/...）+ 旧 UNIQUE。create_all 重建即生效。 | S7（csv_exporter 切换时） | ✅ |
| L04 | S6b | 列表页创建 dialog（`tasks_list.html` 内嵌 `_task_new_dialog.html`）的平台/账号下拉用默认值（未查 DB 传 `platforms`/`accounts`），新建任务只能走默认 platform/account。 | S9 或 UI 增强会话 | ⬜ |
| L05 | S6b | 操作按钮「锁到状态改变」精确语义未达：当前是请求期 disabled（hx-disabled-elt）+ actions 单元格 5s 轮询刷新，≤5s 滞后才换按钮集合；PRD §5.2.3 要「按钮持续 disabled 直到后端状态真实改变再替换」。 | S9/T70 | ⬜ |
| L06 | S4 | 评论 3 次滚动加载（`scroll_collect` max_scrolls=3）语义落在 comments flow，engine 已支持，但 XHS/知乎 comments flow yaml 未加 `scroll_collect` 步骤。 | S5（录制）/S9 | ⬜ |
| L07 | S4 | `scroll_collect` 真增量 XHR：当前对静态 saved 快照重抽 dedup（测边界 20/5）；真实浏览器「滚动触发新 XHR→累积」需 flow 在每次 scroll 后再 `wait_xhr`。 | S5/S9（录制侧真实化） | ⬜ |
| L08 | S5/T27 | 知乎 `{"data":[...]}` 形状不被 `field_extract._find_list_root` 命中（它认 `data.items`/`data` 直接为 list），真实录制后需补 engine 兜底或 map 改 `[*]`。**知乎专项**——用通用 extension 点落地，不 special-case 分支。 | Sz（知乎专项, hold） | ⏸ |
| L09 | S5/T27 | 知乎 maps 当前为骨架，待 LLM mapper 录制替换。**知乎专项**。 | Sz（知乎专项, hold） | ⏸ |
| L10 | S5/T26 | captcha solver wiring：`detect_and_solve` 已实现但未被 handler 调用（契约§5「可选能力默认关」）。wiring（captcha 命中→先 detect_and_solve 再 need_human）未接。 | S9/T71 | ⬜ |
| L11 | S7 | `collection_items.url` NOT NULL 受阻未恢复（L03 的一部分）。根因：`ScrapedPost` schema 无 `url` 字段、engine 不采集 url、`handlers._upsert_post` 硬编码 `url=None`。恢复 NOT NULL 会让每次 upsert_item 触发 IntegrityError。需先给 engine/schema 补 url 采集（S4/S5/Sz 录制侧）后恢复。 | Sz/S9（engine 补 url 采集后） | ⬜ |
| L12 | S8 | `routes/tasks.py api_resume_task` 在 `finally: sess.close()` 后构造 IPC payload 仍访问 `task.account_id/platform/max_posts_per_keyword/download_images/collect_comments`——commit 触发 expire_on_commit 使属性 expired，close 后访问触发 DetachedInstanceError。BDD 7.1 happy-path resume 测试因此跳过（仅留 409 冲突 + 404 缺失分支）。修法：把 payload 字段捕获到局部变量（close 前）或把 IPCRequest 构造移入 try 块。 | S9a | ⬜ |
| L13 | S8探查 | web 侧**无任何 `subprocess.Popen(worker_main)`**——`manifest.WORKER_ENTRY` 只登记不拉起，`app.startup` 只起 watchdog。建任务→`IPCClient.submit` 写 request 文件**无人消费**→任务永远 pending。CLAUDE.local.md 声明「web 按需 Popen」但从未实现。**T70 最大前置**。 | S9a | ⬜ |
| L14 | S8探查 | `worker_main._run_worker` 的 `browser, ctx = await attach(port)` 是局部变量，attach 完丢弃；`serve_worker` 不接收 browser；`handlers._get_engine` 只 `GenericEngine(spec=spec)` 不注入 ctx/page → `engine.page=None` → `_ensure_page` 抛 `RuntimeError("No page available")` → `scrape_task` 立即失败。需 worker 级 ctx 单例 + `_get_engine` 注入 `engine.ctx`/`engine.page`。 | S9a | ⬜ |
| L15 | S8探查 | `handlers._do_qr_login` 只返回路径字符串，不 `page.goto(login_url)`、不 `screenshot` → UI 拿不到真实二维码。登录是全流程起点，stub 则后续全假。需真导航 + 截图存盘。 | S9a | ⬜ |
| L16 | S8探查 | `core/ui/ws.py` 只有 `broadcast(msg)`，**无后台 poll `progress/`+`results/`(ws_events)→WS 推送** 的 relay 循环。worker 写 progress 文件无人读回广播；UI 只能靠 5s 轮询徽章端点拿瞬态，WS 流式进度不工作。需 web 后台 relay loop。 | S9a | ⬜ |

> **收口规则**：某会话收掉一项 → 本表该行状态 ⬜→✅ + 在「当前进度快照」对应会话段记一句「收 L0X（commit `<hash>`）」。**禁止**只改快照不改本表——本表是唯一索引。

## 续接协议（新会话怎么接，3 步开干）

1. **读三件套**（控制上下文量，不重读全部）：① 本文件「⛓️ 共享上下文契约」节（不可漂移）+「🧭 会话经验」扫一眼防错 ② 当前会话 S 段（范围+门禁+T 子任务）③ 目标文件 + 对应 PRD 章节。**+扫「🧯 遗留事项跟踪」表**——看本会话是否要顺手收一项（L0X）。
2. **干完**：勾 S 段 T checklist → 跑 `bash scripts/loop_gate.sh` → 退出 0 才标该 S ✅ → 原子化 commit（`[session SNN] <desc>`）+ push。若收掉某 L0X → 遗留表该行 ⬜→✅ + 快标注「收 L0X（commit `<hash>`）」。
3. **交接**：更新本表该 S 状态行 + 「当前进度快照」；🟡 标 🔄 不标 ✅；❌ 跳过。契约若被改 → 回写「共享上下文契约」节并标 `[契约变更]`。

## 当前进度快照（2026-07-11）

- ⏸ **知乎相关工作 hold**（用户裁决 2026-07-11）：T27 录制、L08 data[] 兜底、L09 maps、知乎 comments scroll_collect 全转 **Sz 知乎专项会话**，暂不挤占 S7-S9 主线。软件扩展性铁律见「⛓️ 共享上下文契约 §[契约变更 2026-07-11 Sz]」——加站只走通用 extension 点（platform.yaml/registry/field_extract/recorder+LLM mapper），禁止 XHS/知乎 special-case 分支。Sz 启动前置：S9/T70 验证 XHS 全链路绿。
- ✅ **S1 完成**（T01-T04：stealth 零注入 / mouse.wheel+smart_wait / 夜间长 sleep / IPC 原语），commit `5de4d26`/`3dba7c3`，分支 `feat/skim-prd-align` 已 push。
- ✅ **S2 完成**（T05 server 读后即焚+坏文件+心跳+control 分发 / T06 心跳看门狗 30s→paused+WS / T07 单任务并发锁(PRD 2.2 pending 排队) / T08 CDPAttachError+worker 优雅退出 / T09 linter 禁 while True/is_captcha/account+auto_then_manual）。新增 `core/ipc/watchdog.py`，全量回归 291 passed，门禁全绿。
  - **裁决记 WHY**：T07 PRD §8.2 场景2.2（B、C 创建为 pending 排队）与原 T07 措辞「拒绝排队」表面矛盾——以 PRD 验收场景为准：创建恒为 pending，仅当无 running 时晋升 running，其余 submit IPC 按 mtime 顺序排队；pending→running 的 pick-up 晋升交给 S4 engine/handler 层（避免与 engine 双重晋升冲突）。
  - T08 task→paused 的「立即性」由 T06 看门狗（≤30s）兜底；精确文案端到端验证属 S9 人工（T70）。
- ✅ **S3 完成**（T10 db.py WAL+check_same_thread+timeout=15 / T11-T13 collection_* 三表：CollectionTask/CollectionItem/CollectionComment，UUID str36 PK + PRD §6 新列 + CASCADE FK + UNIQUE 新名 + 旧列保留过渡 / T14 repository.upsert_item/upsert_comment ON CONFLICT+metrics_json pack / T15 schemas TaskCreate 重构 task_type/target_value/expected_count + http 前缀校验 + count[1,200] 截断）。全量回归 302 passed，门禁全绿。
  - **裁决记 WHY（最小逻辑改动 vs 契约字面）**：PRD §6 全字段重排会破坏 S4/S7 拥有的 handlers/csv_exporter/test_csv_export/test_integration（读写旧列 content/likes/post_id + int-id seeding）。按用户判据「名字差异→存量越小越好；新增→按设计文档」选**保留旧列+加 PRD 新列**过渡：存量消费者零逻辑改动（仅类名+task_id 类型机械替换），PRD 新列/UUID/UNIQUE/WAL/repository 全上。超表过渡态与清理责任见「共享上下文契约 §2 [契约变更 2026-07-10 S3 过渡态]」。
  - **遗留清理**：metrics_json 闲置(待 S4 切聚合) / 旧列+旧 UNIQUE(待 S4/S6/S7 删) / routes 旧 form(待 S6/T32) / PRD NOT NULL(url/platform_comment_id 暂缓,待 S4/S7)。
- ✅ **S4 完成**（T20 单条 try-except→detail_skip_error 进度+continue+计数 / T21 spec 增 `go_back`+`scroll_collect`(max_scrolls=20/empty_break=5) 步骤类型，engine 删 `scrollBy` 改 `mouse.wheel`，滚动边界 dedup+连续空跳出 / T22 `parse_likes`("1.2w"/"1.5万"→int,"赞"→0)+`title_fallback`(content[:20]) / T23 评论 sorted(likes desc)[:20] / T24 新 `risk_probes.py` probe(XHS captcha/知乎 signin/QR)+engine `on_risk` 钩子+`RiskProbeHit`→need_human+`_await_resume` 轮询 control / T25 `_night_sleep_if_quiet` 长睡不抛+主循环+逐条接入）。新增 `modules/collection/risk_probes.py`、`tests/collection/test_risk_probes.py`。全量回归 338 passed，门禁全绿。
  - **裁决记 WHY**：
    - **need_human = handler 内挂起轮询 control，非返回 need_human 结束**。PRD §4.4.2 明令 worker 每 2s 轮询 `control/` 等 resume、resume 后**重跑探针再从当前 URL 续抓**；返回状态结束会丢页面状态、与 PRD §4.4.3「重置探针→从中断 URL 重抓」冲突。故 handler 内 `_handle_need_human`→`_await_resume`(读后即焚 control)→重试同一 ref（engine 再 goto 触发探针）。`_await_resume` 用 `while waiting:` 非 `while True` 以过 §7.4 linter（有 resume/stop 出口，非刷新死循环）。
    - **节律长睡替代抛错**。PRD §4.5.1 是长 `asyncio.sleep` 至 08:00 非 throw-and-retry；`_check_rhythm` 移除 `check_quiet_hours()` raise，quiet 全交 `_night_sleep_if_quiet`（Phase1 前置 + 逐条前置，gate 在任何网络请求前）。`now=` 注入 + 测试 patch 规避墙钟 flaky（会话经验 #7）。
    - **探针粒度=engine 内钩子**。PRD §4.4.1「每次 goto/scroll/click 后立刻探针」粒度细于 handler 外层包；engine 持 `on_risk` 回调在 navigate/scroll/scroll_collect/click 后触发、命中抛 `RiskProbeHit`，handler 统一翻译为 need_human。mock engine 时 `on_risk=None` 不影响现有 engine 单测。
    - **S3 清理**：handlers `_upsert_post` 切 `repository.upsert_item`/`upsert_comment`（content_text/metrics_json 聚合 + `parse_likes` 清洗 + `title_fallback`），删旧列写入引用；`_update_task_progress`/`_complete_task` 写 PRD `actual_count`；`_promote_to_running` 落 pending→running 晋升（S2 T07 遗留）。
  - **遗留清理（S4 后仍 open）**：
    - **route resume→control wiring 缺口**：`routes/tasks.py /api/tasks/{id}/resume` 目前是发新 IPC request（op scrape_task, resume=True），**未**按 PRD §4.4.3 step4 写 `control/ctrl_<rid>.json {action:resume}`。handler `_await_resume` 轮询 control/ 的行为正确，但真实 /resume 路径未对接（handler 测试用直接写 control 文件覆盖）。属 UI/路由 wiring，归 **S6(T32 dialog)/S9(T70 端到验)** 补 resume→control 写入 + UI「我已处理」按钮。
    - **评论 3 次滚动加载**：T23「最多 3 次滚动」语义落在 comments flow 的 `scroll_collect`(max_scrolls=3)；engine 已支持，XHS comments flow yaml 未加 scroll_collect 步骤（录制侧，🟡 S5/T27 知乎录制时统一补）。
    - **model 旧列/NOT NULL 不动**（契约 §2：列删除+url/platform_comment_id NOT NULL 归 S7 csv_exporter 切换时）。
    - **scroll_collect 真增量 XHR**：当前对静态 saved 快照重抽 dedup（测边界 20/5）；真实浏览器「滚动触发新 XHR→累积」需 flow 在每次 scroll 后再 wait_xhr，属录制侧真实化（🟡 S5/S9）。
- 🔄 **S5 进行中**（P2 可选验证码 + 知乎录制）：
  - ✅ **T26 完成**（solver `detect_and_solve(page, ctx, risk_tier, captcha_policy)`：默认 account/manual 命中即 `paused`(=立即 need_human) 不动 slide/ocr；仅 anonymous+auto_then_manual 走 slide/ocr **恰好一次**，失败转 paused 不死循环；click/sms 永远人工。`config.CAPTCHA_DEFAULT_POLICY="manual"`+`CAPTCHA_AUTO_SOLVE_PLATFORMS=[]`；`spec.PlatformSpec` 加 `risk_tier`/`captcha_policy` 字段；XHS yaml 显式 `risk_tier:account`/`captcha_policy:manual`。新 `tests/collection/test_solver.py` 15 用例。全量回归 353 passed，linter 全绿）。
    - **裁决记 WHY（不 wire 进 handler）**：`detect_and_solve` 当前未被 handler 调用（本就是"可选能力沉淀默认关"，契约§5）；S5 仅做 solver+schema+config+yaml，**不**动 `_handle_need_human` 路径，零 S4 回归风险。wiring（captcha 命中→先 detect_and_solve 再 need_human）留 T71 端到验/S9。
  - ⏸ **T27 hold（转 Sz 知乎专项）**（`platforms/zhihu/{__init__.py,platform.yaml}`：search `/api/v4/search_v4`+ItemRef、detail `/api/v4/answers/{id}`+Post.body/interactions、comments `/api/v4/answers/{id}/root_comments`+Comments；`risk_tier:account`/`captcha_policy:manual`）。新 `tests/collection/test_registry_zhihu.py` 只验 yaml 解析+默认值，**不**验 JSON path 正确性。**知乎相关工作暂 hold，等 Sz 专项会话统一排期（用户裁决）**。
    - **遗留（T27 → Sz 知乎专项）**：知乎 `{"data":[...]}` 形状不被 `field_extract._find_list_root` 命中（它认 `data.items`/`data` 直接为 list 未支持），真实录制后需补 engine 兜底或 map 改 `[*]`；maps 当前为骨架待 LLM mapper 录制替换。评论 3 次滚动 `scroll_collect` 待录制侧补。→ 见遗留表 L08/L09/L06（知乎部分）。
- ✅ **S8 完成**（P5 测试门禁：T50 PRD §8 全部 BDD 落 pytest · T51 覆盖率 ≥85%）。新建 `tests/prd_bdd/`（8 文件 16 场景：1.1 未登录拦截/1.2 端口冲突/2.1 输入校验含 SQL 注入/2.2 并发排队/3.1 脏文件容错/3.2 读后即焚/4.1 Timeout 跳过/4.2 滚动边界/5.1 点赞清洗/5.2 缺失 DOM 兜底/6.1 会话过期/6.2 防暴力红线/7.1 日限额 wiring/7.2 随机延迟/8.1 空数据防御/8.2 多行字符）。T51 补测：新 `test_ua_pool/test_recorder/test_handlers_helpers/test_engine_extra/test_routes_collection/test_misc_modules/test_slide_solver/test_field_extract_extra`，覆盖率 67%→86%。`loop_gate.sh` 接 `--cov-fail-under=85` + `pyproject` 加 `pytest-cov`/`fail_under=85`。全量回归 + 约束 linter + cov 门全绿。
  - **裁决记 WHY（7.1 wiring 修复，本会话唯一非测试代码改动）**：探查发现 BDD 7.1 实现缺口——`check_daily_limit` 抛的 `DailyLimitError` 被 `_check_rhythm` 的 `except Exception: pass` 吞掉，且 `daily_scrape_count` 列**从未被任何代码自增**，日限额红线双重失效。用户裁决「S8 顺手修 wiring + 落 BDD」。修法：`_check_rhythm` 改查 SQLite 当日 `collection_items` COUNT（跨任务累加，绕开永不自增的 `daily_scrape_count` 列），不吞 DailyLimitError；per-note 循环内调用使「第 50 条命中 200」成立；handler catch → task `paused`（区别 captcha 的 need_human，PRD §7.1 明令 paused）+ daily_limit progress + return。BDD 7.1 驱动真实路径（不 patch `_check_rhythm`）：seed 150 条今日 item，跑 task B 100 refs → 第 51 ref 命中 200 → paused + posts_scraped=50 + daily_limit 文案。详见「共享上下文契约 §[契约变更 2026-07-11 S8]」。
  - **裁决记 WHY（覆盖口径全包 85%）**：用户裁决全包 85%（含 hold/可选模块）。recorder 只测纯逻辑/可 mock 部分（`_build_recording_result`/`_make_save_as_name`/`_guess_flow_name`/`record_*`/`_on_response` mock response/`capture_element_selectors` mock element）；`start`/`stop`/`_launch_chrome_and_attach` 真浏览器路径不测（接受残留 67%）。slide_solver 用 sys.modules 注入 fake cv2/numpy/playwright 覆盖 ImportError + elements-missing 分支 + `_execute_slide` track；真 cv2 gap 检测路径不测（cv2 未装）。
  - **遗留 L12**：`api_resume_task` post-close 访问 task 属性 → DetachedInstanceError（S8 测试发现的真 bug，超出 7.1 wiring 范围，登记 L12 归 S9/后续）。BDD 7.1 resume happy-path 跳过，留 409/404 分支。

- ⬜ **下一会话 = S9a**（P7.5 端到端 wiring 修复，T70 前置）。探查发现 4 个 P0/P1 硬阻塞（L13-L16）使「登录跑全流程」根本启动不了，必须先于 T70 人工验修复。范围：L13 web→worker Popen · L14 ctx 注入 engine · L15 login QR 真实化 · L16 WS progress relay · 收 L01/L10/L12。依赖 S2-S8✅，🟡 半自动（代码可自动、需真 Chrome 人验启）。门禁 全量回归 + 启 Chrome 烟测。
- ⬜ S9（P6 文档 + P7 人验）后置到 S9a✅ 之后。

- ✅ **S7 完成**（P4 CSV 宽表 + L03 旧列收口）。T40 `csv_exporter.py` 整体重写：删 AI/Excel 双模式，改为单一左连接宽表导出，10 列中文表头（`平台/笔记ID/笔记标题/笔记正文/笔记点赞数/笔记发布时间/笔记链接/评论者昵称/评论内容/评论点赞数`，PRD §4.6.3），读 PRD 列 `content_text`/`metrics_json`(解出 likes)/`publish_time`/`url` + 评论按 `item_id` join 读 `author_name`/`content_text`/`like_count`(desc)；左连接 N 评论→N 行、0 评论→1 行评论列空（PRD §4.6.2）；`utf-8-sig` BOM + `csv.DictWriter` 转义 emoji/逗号/引号（PRD §8.6）；0 条→`EmptyExportError`。T41 `routes/export.py` 去 `format` 参数，0 条→`400 JSON {ok:false,error}` 供前端 Toast；导出按钮由 `<a>` 改 `<button onclick="exportCsv(tid,this)">`，`app.js` 新增全局 `exportCsv`（fetch→200 blob 下载 / 400 `showToast` 复用 S6）；`tasks._actions_html` + `task_detail.html` 两按钮合一「导出 CSV」。L03 收口：`models/post.py`+`comment.py` 删全部旧列（content/likes/collects/comments_count/shares/tags/post_type/image_count/image_urls/local_images/published_at/raw_json/keyword_id/created_at + comment 的 post_id/platform_id/content/likes/sub_comment_count/is_author_liked/rank/published_at/raw_json/created_at）+ 删旧 `UNIQUE(post_id,platform_id)`；`platform_comment_id` 改回 NOT NULL（handler 总填 `c_pid or synth_{rank}`）；连带迁 `routes/posts.py`（`page_posts` 去 keyword 过滤、按 likes desc；`page_post_detail` 改 `item_id`/`like_count` desc）+ `posts.html`/`post_detail.html` 到 PRD 列；重写 `test_csv_export.py`（10 表头+左连接+转义+空 400+路由）+ `test_contract_core.test_dm02` 改断言旧列已删/NOT NULL 恢复。全量回归 385 passed，门禁全绿。
  - **裁决记 WHY**：
    - **单一宽表替代 AI/Excel 双模式**。PRD §4.6 只规定左连接宽表一种交付格式（§4.6.3 表头表是唯一规格）；AI 模式（pipe-joined top_comments）与 Excel ZIP 是 PRD 前的存量，PRD 未保留。按契约「新增→按设计文档」裁决**重写而非保留**，删双模式+`export_empty_db`，`export/__init__.py` 同步收口导出面。
    - **0 条→400 JSON + fetch 下载，非 `<a>` 直链**。PRD §4.6「0 条→拦截+Toast」要求空数据时前端弹 Toast；`<a href>` 直链点开 400 JSON 是裸 JSON 页，无法 Toast。故导出按钮改 `<button>` + `exportCsv` JS fetch：200 触发 blob 下载（保留文件下载语义），400 复用 S6 `showToast` 弹「暂无可导出的采集数据」。
    - **`url` NOT NULL 受阻，保留 nullable + 新增 L11**。L03 字面要 `url` NOT NULL，但实测 `ScrapedPost` schema 无 `url` 字段、engine 不采集、`_upsert_post` 硬编码 `url=None`——恢复 NOT NULL 会让每次 `upsert_item` IntegrityError，崩 handlers + 全部 handler 测试。属 engine/录制侧能力缺口（非 CSV 层），按会话经验 #3 显式裁决：**不默默选**，保留 nullable、登记 L11 归 Sz/S9（engine 补 url 采集后恢复），而非硬上 NOT NULL 制造回归。`platform_comment_id` NOT NULL 安全（handler 总填），已恢复。
    - **连带迁 posts 路由/模板非 scope 蔓延**。删模型旧列会让 `routes/posts.py`（`page_posts` keyword 过滤读 `keyword_id`、`page_post_detail` 读 `post_id`/`rank`）+ `posts.html`/`post_detail.html` 崩。这些消费者与 L03 同源（旧列），不迁则旧列不能删——属 L03 收口的必要前置，非新功能。PRD §4.6.1 数据预览本就「按 likes desc、列标题/作者/正文摘要/点赞/评论数」，故 `page_posts` 顺势去 keyword 过滤、改 likes desc（metrics 在路由侧解一次传模板），与 PRD 对齐。
  - **遗留**：`collection_items.url` NOT NULL 未恢复 → L11（归 Sz/S9 engine 补 url 采集后）；导出按钮「禁用」语义当前是 fetch 400 后 Toast（非按钮 preemptive disabled），精确「无数据时按钮灰」归 S9/T70 UI 增强或后续。

- ✅ **S6b 完成**（P3.5 任务控制台列表页，复用 S6 徽章端点）。T37 新 `GET /tasks` + `tasks_list.html`（任务ID/平台/类型/目标值/进度 actual/expected/状态徽章/操作 七列表，行点击→详情，空状态卡片「暂无采集任务…」+【新建任务】）；`<td id="status-<id>">` 内嵌 S6 自轮询徽章 span，`<td id="actions-<id}">` 5s 轮询 `/api/tasks/{id}/actions` 局部刷新。T38 新 `GET /api/tasks/{id}/row`（整 `<tr>` 片段，单一来源 `_task_row.html` 局部，`env.get_template().render()` 同时供列表初始渲染与创建后 afterbegin 插入）+ `GET /api/tasks/{id}/actions`（操作按钮片，按 status：running→取消/need_human→唤起+已处理/failed·error·paused→继续/completed→导出，`hx-disabled-elt`+`lockBtn` 乐观锁）。T39 抽 `_task_new_dialog.html` 局部（从 task_new.html 提取 dialog+校验+二次确认+提交 JS），task_new.html 与 tasks_list.html 都 include；成功后若 `#tasks-tbody` 存在→`htmx.ajax` afterbegin 插 `/api/tasks/{id}/row` + 绿 Toast「任务已就绪」（PRD §5.3.2），否则维持「查看详情」链接。新增 `_actions_html`/`_row_context` helper。test_routes 43 用例（+10），全量回归 382 passed，门禁全绿。
  - **裁决记 WHY**：
    - **`<tr>` 单一来源用局部模板**。列表初始渲染（GET /tasks 服务端循环 include）与 `/row` 端点（创建后 afterbegin 插入）共用 `_task_row.html`，`/row` 用 `templates.env.get_template().render()`，避免模板与 Python 双写 `<tr>` 漂移。
    - **actions 单元格轮询而非 POST 后显式刷新**。PRD §5.2.3「轮询状态真实改变后 HTMX 替换 DOM」——actions `<td>` 5s 轮询 `/api/tasks/{id}/actions` 即满足：POST（cancel/resume）落地后，下次轮询把按钮换成新 status 的集合。按钮 `hx-disabled-elt` 处理请求期 disabled，无需改 cancel/resume 端点返回类型（仍 JSON，task_detail S6 不受影响）。
    - **创建 dialog 抽局部而非双写**。tasks_list.html 内嵌创建 dialog（满足 PRD §5.3.2「列表页点+新建→dialog→afterbegin」），与 /tasks/new 共用 `_task_new_dialog.html`；局部用 `(platforms or [])` 防御未传上下文，列表页 dialog 走默认 platform/account（MVP 够用，真实账号下拉归后续增强）。
    - **lockBtn 全局函数**。actions 片段由端点返回、htmx swap 进 DOM，按钮 `onclick="lockBtn(this)"` 调页面脚本定义的 `lockBtn`（设 aria-busy），避免 Python f-string 转义引号地狱。
  - **遗留**：列表页 dialog 的平台/账号下拉默认值（未查 DB）；「按钮持续 disabled 到状态改变」当前是请求期 disabled+轮询刷新（≤5s 滞后），精确「锁到状态变」归 S9/T70 端到验。

- ✅ **S6 完成**（P3 UI 行为，保留 WS + 补 HTMX 局部交互）。T30 base.html 引 htmx.org@2.0.4 CDN（之前 hx- 属性因无 htmx.js 而失效）；T31 status badge：新 `GET /api/tasks/{id}/status` 返回可轮询 `<span>` 片段（pending/running/need_human 红blink/paused/completed/error），running 时读 `progress/<rid>.json` 取 night_sleep 深色+07:00 / resting 文案；style.css 补 `.badge` 全套 + `@keyframes badge-blink`；T32 `task_new.html` 改 `<dialog>` + 失焦 http 校验 + count≤200 截断 + 耗时预估二次确认 modal + aria-busy，`api_create_task` form 迁 PRD §4.1.1（task_type/target_value/expected_count）删 TaskKeyword 建链、IPC 派生 keywords 兼容 S4；T33 task_detail 操作按钮 `hx-disabled-elt` 乐观锁 + need_human 高亮【唤起浏览器】/【已处理，继续】；T34 `posts.html` 行点击 `htmx.ajax` master-detail + 新 `GET /api/items/{id}/comments` 返回 colspan=7 `<tr>` 片段（0 评论置灰）；T35 base.html 导航底部心跳灯 `hx-get=/api/heartbeat every 10s` + 新端点读 `heartbeat_age()` <30s 绿/≥30s红灰；T36 app.js 监听 `htmx:responseError/sendError`→右上红 Toast 3s，`showToast` 挂 window + 支持 duration。新增 `collection_tasks.request_id` 列（[契约变更 2026-07-11 S6]）。test_routes 33 用例（+19），全量回归 372 passed，门禁全绿。
  - **裁决记 WHY**：
    - **保留 WS + 补 HTMX**。PRD §5 前言「禁 WS，全用 HTMX/SSE」，但契约 §7/§8 已裁决保留 WS（流式进度本质更适合，推翻是纯重写零新能力）。S6 循契约：WS 留 app.js 流式进度，HTMX 负责徽章/dialog/master-detail/心跳/Toast，全程无整页 reload。
    - **T34 落 posts.html 非 post_detail.html**。master-detail「点主行→子行插入」是列表行模式；post_detail 是单篇已内联评论，行展开无意义。按 PRD §5.4.2 语义落 posts.html 表格行。
    - **T32 删建链不 drop 模型**。契约 §2 写「S6 删 routes 旧 form/TaskKeyword/Keyword 链」——精确理解为删 `api_create_task` 里 upsert Keyword + 建 TaskKeyword 链那段，**不** drop Keyword 模型（`posts.py /posts` 筛选仍读 `Keyword.text`，drop 会崩筛选）。模型删除归后续清理。
    - **加 request_id 列非删列**。progress 文件按 request_id 命名、data 里 task_id 被 night_sleep 覆写丢失，要关联 task↔progress 取瞬态须存 rid。加列 additive、nullable，不破坏 S3 契约，且为 S9 resume→control wiring 铺路。
    - **resume→control wiring 留 S9**。需 worker 当前 request_id 追踪，属端到验 wiring（S4 遗留同此裁决）；S6 只做 UI 乐观锁 + 按钮视觉，`task.request_id` 已存，S9 接 `control/ctrl_<rid>.json` 即可。
  - **遗留清理（S6 后 open）**：
    - **resume→control 文件 wiring**：`/api/tasks/{id}/resume` 仍发新 IPC request（非 PRD §4.4.3 的 `control/ctrl_<rid>.json {action:resume}`）。归 S9/T70。
    - **JS 行为端到验**：dialog 校验/耗时 modal/乐观锁/master-detail toggle/Toast 触发等 JS 运行时行为，pytest 只断言静态接入（渲染含 `<dialog>`/`hx-*`/app.js 含监听串），真实浏览器行为归 S9/T70。
    - **model 旧列 + NOT NULL**（契约 §2：url/platform_comment_id NOT NULL、旧列删除）仍 open，归 S7 csv_exporter 切换时。
    - **/tasks 列表页 → 已完成 S6b**：PRD §5.2「任务控制台」列表页（`<td id=status-<id>>` 徽章轮询）S6 未顺带补，已立项并在 S6b 完成（GET /tasks + 行/操作片段端点 + 创建→afterbegin 插行）。
