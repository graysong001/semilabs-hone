# PRD_ALIGN_TASKS — Skim 采集模块 PRD 对齐任务切分

> **单一进度真相源**（与 PRD 对齐迭代）。每次会话查本表选一个 ⬜ 任务，做完跑门禁、更新本表状态、commit+push。  
> **切分原则**：每个任务 = 一个会话上下文可完成的闭环（少量文件 + 一个 pytest 门禁），防止单会话上下文溢出引发幻觉。  
> 设计裁决依据：`docs/semilabs_hone_skim_sepc.md`（PRD）+ 本仓库 `~/.claude/plans/semilabs-hone-docs-semilabs-hone-skim-s-snoopy-swan.md`（已批准）。  
> 取舍总纲：**PRD 安全/正确性红线全盘接受并推翻已实现违规代码；架构层（WS 推送、recorder+LLM 通用引擎）保留只补 PRD 行为；验证码自动解作为可选能力沉淀（默认关）**。

---

## 状态图例

⬜ 未开始　🔄 进行中/代码完成待人工验　✅ 完成　⛔ 阻塞

## 门禁定义（Definition of Done）

每个任务 done = 机器可判 0/1：
1. **约束 linter**：`python3 scripts/check_constraints.py` 退出 0。
2. **任务门**：本表「门禁」列 pytest 退出 0。
3. **全量回归**：`bash scripts/loop_gate.sh` 退出 0（约束 + 全量 pytest，不破坏已✅任务）。
4. **可自动**：✅=loop 全程自动；🟡=loop 写代码+单测、done 需人工签；❌=纯人工 loop 跳过。

**熔断**：单任务 3 次不过门 → ⛔ 停、报根因、等介入（CLAUDE.md §错误熔断）。

---

## P0 — 安全红线与正确性修复（最高优先）

> 目标：消除封号风险源 + 卡死 bug。不动表结构。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T01 stealth 零注入 | ✅ | ✅ | — | anti_detect/stealth.py 降级 no-op | test_fingerprint+test_contract_collection |
| T02 拟人滚动/点击/等待 | ✅ | ✅ | T01 | human_behavior.py mouse.wheel + smart_wait | test_human_behavior |
| T03 夜间长 sleep | ✅ | ✅ | — | rhythm.py is_quiet_hours+sleep_until_wakeup | test_rhythm |
| T04 IPC 原语 | ✅ | ✅ | — | paths.py control_dir/burn/heartbeat/bad-JSON | test_ipc |
| T05 IPC server 读后即焚+坏文件+心跳 | 🔄 | ✅ | T04 | server.py: 请求加载后立即 burn；坏 JSON catch+burn；serve 循环每 10s write_heartbeat；control 文件读后即焚分发 pause/resume/stop | test_ipc(新增 server 读后即焚+坏文件+心跳用例) |
| T06 心跳看门狗 | ⬜ | ✅ | T04,T05 | client.py poll_heartbeat；web 侧 30s 过期→DB task running→paused + WS 广播「引擎异常中断」 | test_ipc(看门狗) + test_routes |
| T07 单任务并发锁 | ⬜ | ✅ | — | routes/tasks.py 建/启动前查 status=running 计数>0 拒绝排队（PRD 8.2 场景2.2） | test_routes |
| T08 cdp 端口冲突 | ⬜ | ✅ | — | browser/cdp.py 9333-9340 探测递增；CDP 连接失败→paused+UI 提示关 Chrome（PRD 8.1 场景1.2） | test_cdp |
| T09 约束 linter 扩展 | ⬜ | ✅ | T01 | check_constraints.py 禁 while True/is_captcha 死循环；禁 account 站点 captcha_policy=auto_then_manual | check_constraints.py |

## P1 — 数据模型对齐（PRD §6）

> 不 DROP 旧表（database.md 红线）：新增 collection_* 表 + 数据搬迁 + 切换读写 + 旧表 deprecated。

| 任务 | 状态 | 可自动 | 依赖 | 范围/文件 | 门禁 |
|---|:-:|:-:|---|---|---|
| T10 DB 引擎 WAL | ⬜ | ✅ | — | models/db.py check_same_thread=False+timeout=15+PRAGMA journal_mode=WAL | test_models |
| T11 collection_tasks 表 | ⬜ | ✅ | T10 | models/task.py CollectionTask(UUID PK, task_type/target_value/expected/actual/error_msg/updated_at) | test_models |
| T12 collection_items 表 | ⬜ | ✅ | T11 | models/post.py→collection_item.py metrics_json TEXT+publish_time VARCHAR+UNIQUE(platform,platform_id) | test_models |
| T13 collection_comments 表 | ⬜ | ✅ | T12 | models/comment.py→collection_comment.py UNIQUE(item_id,platform_comment_id) | test_models |
| T14 repository upsert | ⬜ | ✅ | T12,T13 | models/repository.py ON CONFLICT DO UPDATE upsert_item/upsert_comment | test_models |
| T15 schemas 校验 | ⬜ | ✅ | T11 | schemas.py TaskCreate 校验 http 前缀+count[1,200]截断 | test_routes |
| T16 数据搬迁脚本 | ⬜ | ✅ | T11-T13 | scripts/migrate_to_collection_tables.py 幂等搬迁 | 运行脚本+断言行数 |

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

## 依赖 DAG（任务级）

```
T01 → T02
T04 → T05 → T06 → T35
T05 → T24 → T26
T10 → T11 → T12 → T13 → T14 → (T20,T23,T40)
T11 → T15 → T32
T30 → (T31,T32,T33,T34,T36)
T22 → T23 → T27(🟡)
T07（独立）
T08（独立）
T09（独立, 依赖 T01）
```

**可并行批次**：A:T01,T03,T04,T07,T08,T09 → B:T02,T05,T10 → C:T06,T11,T22,T24 → D:T12,T14,T25,T26 → E:T13,T20,T23,T30 → F:T31-T36,T40 → G:T50,T51。

## 续接协议（新会话怎么接）

1. 读 `~/.claude/plans/semilabs-hone-docs-semilabs-hone-skim-s-snoopy-swan.md`（裁决）+ 本表（选任务）。
2. 选一个 ⬜ 且依赖全✅ 的任务（loop 只选 ✅ 列）。
3. 读目标文件 + 对应 PRD 章节。
4. 实现 → 跑 `bash scripts/loop_gate.sh` → 退出 0 才标 ✅ → commit + push。
5. 🟡 任务 loop 标 🔄，不标 ✅；❌ 任务跳过。
6. 跨会话交接：本表状态行 + commit message 标 `[task TNN]`。

## 当前进度快照（2026-07-10）

- ✅ 完成：T01,T02,T03,T04（P0 反检测+节律+IPC 原语），已 commit `5de4d26`/`3dba7c3`，分支 `feat/skim-prd-align`。
- 🔄 进行中：T05（IPC server 读后即焚 wiring）—— 原语已就绪（T04），server.py 接入待下一会话。
- 下一个会话首选：**T05**（依赖 T04 已✅，可自动✅，范围聚焦 server.py 单文件+test_ipc）。
