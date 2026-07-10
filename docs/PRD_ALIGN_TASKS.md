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

⬜ 未开始　🔄 进行中/代码完成待人工验　✅ 完成　⛔ 阻塞

## 门禁定义（Definition of Done）

每个**会话 S** done = 机器可判 0/1：
1. **约束 linter**：`python3 scripts/check_constraints.py` 退出 0。
2. **会话门**：该 S 段列出的 pytest 目标退出 0。
3. **全量回归**：`bash scripts/loop_gate.sh` 退出 0（约束 + 全量 pytest，不破坏已✅会话）。
4. **可自动**：✅=loop 全程自动；🟡=loop 写代码+单测、done 需人工签；❌=纯人工 loop 跳过。

**熔断**：单会话 3 次不过门 → ⛔ 停、报根因、等介入（CLAUDE.md §错误熔断）。

---

## 会话级编排（S1-S9 = 9 个有界会话）

> 每个 S = 一个会话上下文。内含 T 子任务作为会话内 checklist（勾完即 done）。下一会话只接一个 S。

| 会话 | 状态 | 可自动 | 依赖 | 主题 | 范围 | 会话门（pytest） |
|---|:-:|:-:|---|---|---|---|
| S1 | ✅ | ✅ | — | P0 反检测+节律+IPC 原语 | T01-T04 | test_human_behavior/test_rhythm/test_fingerprint/test_ipc |
| S2 | ✅ | ✅ | S1 | P0 IPC/安全 wiring | T05 server 读后即焚+坏文件+心跳+control 分发 · T06 心跳看门狗 30s→paused · T07 单任务并发锁 · T08 cdp 端口冲突 · T09 约束 linter 扩展 | test_ipc/test_routes/test_cdp/check_constraints |
| S3 | ⬜ | ✅ | S2 | P1 数据模型对齐 | T10 WAL · T11-T13 collection_* 三表原地改名(UUID+UNIQUE+metrics_json+publish_time VARCHAR) · T14 repository upsert · T15 schemas 校验 | test_models/test_routes |
| S4 | ⬜ | ✅ | S3 | P2 引擎+探针+节律 | T20 单条跳过+计数 · T21 go_back+滚动边界20/5+删 scrollBy · T22 parse_likes/title_fallback · T23 评论 Top20 · T24 风控探针 · T25 节律暖场接入主循环 | test_engine/test_field_extract/test_rhythm + 新 test_risk_probes |
| S5 | ⬜ | 🟡 | S4 | P2 可选验证码 + 知乎录制 | T26 solver 风险分层(risk_tier/captcha_policy 默认 manual) · T27 🟡 知乎录制 platform.yaml | 新 test_solver + 人验 zhihu |
| S6 | ⬜ | ✅ | S3,S2 | P3 UI 行为（保留 WS） | T30 htmx+Pico · T31 状态徽章 · T32 创建任务 dialog+校验+耗时 modal · T33 乐观锁 · T34 master-detail 评论 · T35 心跳指示灯 · T36 错误 Toast | test_routes |
| S7 | ⬜ | ✅ | S3 | P4 CSV 宽表 | T40 宽表重写(10 中文表头+utf-8-sig+转义) · T41 导出路由+空数据防御 | test_csv_export/test_routes |
| S8 | ⬜ | ✅ | S4-S7 | P5 测试门禁 | T50 PRD §8 全部 BDD 落 pytest · T51 覆盖率 ≥85% | tests/prd_bdd/ + cov 门 |
| S9 | ⬜ | 🟡/❌ | S2-S8 | P6 文档同步 + P7 端到验 | T60-T63 spec/design/context 自洽 · T70 ❌ 端到端 · T71 ❌ 验证码可选能力验证 | 文档自洽 + 人工 |

**loop 顺序**：S1 → S2 → S3 → S4 → S5(代码半,录制🟡) → S6 → S7 → S8 → S9。  
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
| T10 DB 引擎 WAL | ⬜ | ✅ | — | models/db.py check_same_thread=False+timeout=15+PRAGMA journal_mode=WAL | test_models |
| T11 collection_tasks 表 | ⬜ | ✅ | T10 | models/task.py CollectionTask(UUID PK, task_type/target_value/expected/actual/error_msg/updated_at) | test_models |
| T12 collection_items 表 | ⬜ | ✅ | T11 | models/post.py→collection_item.py metrics_json TEXT+publish_time VARCHAR+UNIQUE(platform,platform_id) | test_models |
| T13 collection_comments 表 | ⬜ | ✅ | T12 | models/comment.py→collection_comment.py UNIQUE(item_id,platform_comment_id) | test_models |
| T14 repository upsert | ⬜ | ✅ | T12,T13 | models/repository.py ON CONFLICT DO UPDATE upsert_item/upsert_comment | test_models |
| T15 schemas 校验 | ⬜ | ✅ | T11 | schemas.py TaskCreate 校验 http 前缀+count[1,200]截断 | test_routes |
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
| T26 可选验证码能力 | ⬜ | ✅ | T24 | captcha/solver.py detect_and_solve 加 risk_tier/captcha_policy 参数，默认 manual→立即 need_human；anonymous+auto_then_manual 才走 slide/ocr 失败1次转人工；platform.yaml 新增 risk_tier/captcha_policy 字段 | test_solver(新) |
| T27 知乎适配器 | 🔄 | 🟡 | T22-T25 | 录制 search/detail/comments 三 flow 生成 platforms/zhihu/platform.yaml（人工录制） | registry 加载+人验 |

## P3 — UI 行为对齐（PRD §5，保留 WS）

> 不重写传输层；WS 流式进度 + HTMX 局部交互并存。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T30 HTMX+Pico 引入 | ⬜ | ✅ | — | base.html 加 htmx.js + Pico CSS；保留 app.js(WS) | test_routes(渲染 200) |
| T31 状态徽章 | ⬜ | ✅ | T30 | task_detail.html need_human 红闪烁/night_sleep 深色+07:00文案/pending/running/paused/completed/error | test_routes |
| T32 创建任务 dialog | ⬜ | ✅ | T15,T30 | task_new.html 改 dialog+失焦校验(http/count≤200)+耗时预估二次确认 modal+aria-busy | test_routes |
| T33 操作按钮乐观锁 | ⬜ | ✅ | T07,T30 | hx-post 后 aria-busy+disabled；need_human 高亮唤起/已处理继续 | test_routes |
| T34 master-detail 评论 | ⬜ | ✅ | T13,T30 | post_detail.html 行点击 hx-get=/api/items/<id>/comments hx-swap=afterend；无评论置灰 | test_routes |
| T35 全局心跳指示灯 | ⬜ | ✅ | T06,T30 | base.html 导航栏底部轮询 heartbeat 绿/红灰+「引擎离线」 | test_routes |
| T36 全局错误 Toast | ⬜ | ✅ | T30 | app.js/inline 监听 htmx:responseError/sendError→右上红 Toast 3s | test_routes |

## P4 — CSV 宽表交付（PRD §4.6）

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T40 CSV 宽表重写 | ⬜ | ✅ | T12,T13 | csv_exporter.py 左连接宽表(一行一评论/0评论1行)+10中文表头+utf-8-sig+csv转义emoji/逗号/引号 | test_csv_export |
| T41 导出路由+空数据防御 | ⬜ | ✅ | T40 | routes/export.py 0条→特定码+前端 Toast；按钮禁用 | test_routes+test_csv_export |

## P5 — 测试与约束门禁

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T50 PRD 第8章 BDD | ⬜ | ✅ | 各任务 | 把 PRD §8 全部 Given-When-Then 落 pytest(1.1/1.2/2.1/2.2/3.1/3.2/4.1/4.2/5.1/5.2/6.1/6.2/7.1/7.2/8.1/8.2) | tests/prd_bdd/ |
| T51 覆盖率≥85% | ⬜ | ✅ | 各任务 | pytest --cov=semilabs_hone --cov-fail-under=85 | cov 门 |

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
S1 → S2 → S3 → S4 → S5(🟡) → S6 → S7 → S8 → S9(🟡/❌)
        \____ S3 → S6（UI 依赖表）; S3 → S7（CSV 依赖表）; S2 → S6（心跳指示灯）
```

> **不并行**：MVP 单浏览器 + 共享同一批文件/DB/IPC 契约，同一时刻只推进一个 S。  
> 任务级细 DAG（T 子任务内部依赖）见各 P 段表格的「依赖」列，仅作会话内排序参考。

## 续接协议（新会话怎么接，3 步开干）

1. **读三件套**（控制上下文量，不重读全部）：① 本文件「⛓️ 共享上下文契约」节（不可漂移）+「🧭 会话经验」扫一眼防错 ② 当前会话 S 段（范围+门禁+T 子任务）③ 目标文件 + 对应 PRD 章节。
2. **干完**：勾 S 段 T checklist → 跑 `bash scripts/loop_gate.sh` → 退出 0 才标该 S ✅ → 原子化 commit（`[session SNN] <desc>`）+ push。
3. **交接**：更新本表该 S 状态行 + 「当前进度快照」；🟡 标 🔄 不标 ✅；❌ 跳过。契约若被改 → 回写「共享上下文契约」节并标 `[契约变更]`。

## 当前进度快照（2026-07-10）

- ✅ **S1 完成**（T01-T04：stealth 零注入 / mouse.wheel+smart_wait / 夜间长 sleep / IPC 原语），commit `5de4d26`/`3dba7c3`，分支 `feat/skim-prd-align` 已 push。
- ✅ **S2 完成**（T05 server 读后即焚+坏文件+心跳+control 分发 / T06 心跳看门狗 30s→paused+WS / T07 单任务并发锁(PRD 2.2 pending 排队) / T08 CDPAttachError+worker 优雅退出 / T09 linter 禁 while True/is_captcha/account+auto_then_manual）。新增 `core/ipc/watchdog.py`，全量回归 291 passed，门禁全绿。
  - **裁决记 WHY**：T07 PRD §8.2 场景2.2（B、C 创建为 pending 排队）与原 T07 措辞「拒绝排队」表面矛盾——以 PRD 验收场景为准：创建恒为 pending，仅当无 running 时晋升 running，其余 submit IPC 按 mtime 顺序排队；pending→running 的 pick-up 晋升交给 S4 engine/handler 层（避免与 engine 双重晋升冲突）。
  - T08 task→paused 的「立即性」由 T06 看门狗（≤30s）兜底；精确文案端到端验证属 S9 人工（T70）。
- ⬜ **下一会话 = S3**（P1 数据模型对齐：T10 WAL · T11-T13 collection_* 三表原地改名 · T14 repository upsert · T15 schemas 校验）。依赖 S2 已✅，可自动✅。范围聚焦 `models/db.py`+`task.py`+`post.py`+`comment.py`+`repository.py`+`schemas.py`，门禁 test_models/test_routes。
