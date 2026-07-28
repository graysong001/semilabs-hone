# DEV_PLAN — 研发计划与进度跟踪

> 这是 semilabs-hone 的**单一进度真相源**。每次会话开始查此表选模块，结束更新此表。
> 模块切分依据 docs/skim_design.md §18，按依赖 DAG 拆成 12 个**单会话可完成**的独立单元。

> ⚠️ **当前主线 = PRD 对齐迭代**（`docs/semilabs_hone_skim_sepc.md` 8 章 PRD 与已建代码存在根本性冲突）。
> 进度跟踪已迁移到 **[docs/PRD_ALIGN_TASKS.md](PRD_ALIGN_TASKS.md)**（任务级切分，跨会话防上下文溢出）。
> 下方原 12-DM 模块表为骨架建设期的历史记录；PRD 对齐期间以 PRD_ALIGN_TASKS 为准，本表里程碑章节同步更新。

---

## Definition of Done（防止 loop 跑偏烧 token）

**每个模块的 done 必须是机器可判定 0/1 的门，不是散文。** 三扇门：

1. **约束 linter**：`python3 scripts/check_constraints.py` 退出 0（禁 launch/stealth/automation flag/WebGL伪造/硬编码密钥 + cdp.py 参数白名单）。对应 PROJECT_CONTEXT §3。
2. **自动门命令**：本表"自动门"列（该模块的 pytest 目标）退出 0。
3. **全量回归**：`scripts/loop_gate.sh` 退出 0（约束 linter + 全量 pytest，确保不破坏已✅模块）。

**`可自动交付` 列含义**：
- ✅ = loop 可全程自动标 done（纯代码，自动门可达）。
- 🟡 = loop 写代码+单测，**done 需人工签**（端到端/真实环境验证；loop 标"🔄 代码完成待人工验"而非 ✅）。
- ❌ = 纯人工，**loop 跳过**（扫码/录制/真触发）。

**loop 规则**：
- 每轮选 `可自动交付=✅` 且依赖全✅ 的模块 → 实现 → 跑 `loop_gate.sh` → 退出 0 才标 ✅ + commit + push。
- 单模块 3 次不过 → 标 ⛔ 停，报根因等介入。
- 不碰 🟡/❌ 的端到验；🟡 只做代码+单测半，标 🔄。
- 覆盖率门（M4）：核心业务/API ≥85%（见 .claude/rules/testing.md）。

---

## 状态图例

⬜ 未开始　🔄 进行中/代码完成待人工验　✅ 完成　⛔ 阻塞

## 模块进度表

| DM | 模块 | 状态 | 可自动交付 | 依赖 | 自动门命令 | 人工门 | spec |
|----|------|------|:-:|------|-----------|--------|------|
| 01 | core 基座 | ✅ | ✅ | — | `pytest tests/core/test_retry.py -q` | — | [01](modules/01-core-foundation.md) |
| 02 | 数据模型 | ✅ | ✅ | 01 | `pytest tests/core/test_models.py -q` | — | [02](modules/02-data-models.md) |
| 03 | IPC 总线 | ✅ | ✅ | 01 | `pytest tests/core/test_ipc.py -q` | — | [03](modules/03-ipc-bus.md) |
| 03a | IPC 总线补强（F1/F3/F9/F11：账号路由、cancel 即时化、captcha paused、gc/idle 退出/consume-on-read） | ✅ | ✅ | 03 | `pytest tests/core/test_ipc.py -q` | — | [FIX_PLAN](FIX_PLAN.md) |
| 04 | Web 外壳 | ✅ | ✅ | 02,03 | `pytest tests/core/test_routes.py -q` | serve 起+人看渲染 | [04](modules/04-web-shell.md) |
| 05 | 采集-浏览器 | 🔄 | 🟡 | 01,03 | `pytest tests/collection/test_cdp.py -q` | 真 Chrome+扫码+navigator.webdriver | [05](modules/05-collection-browser.md) |
| 05a | 浏览器接线补强（F2/F5/F6：CLI serve/worker 接线、supervisor/tracker、登录三级经 ctx 真实化、按账号指纹+CDP Emulation apply） | 🔄 | 🟡 | 05 | `pytest tests/collection/test_integration.py -q` | 真扫码登录端到端 | [FIX_PLAN](FIX_PLAN.md) |
| 06 | 采集-反检测 | ✅ | ✅ | 05 | `pytest tests/collection/test_human_behavior.py tests/collection/test_fingerprint.py -q` | 真实注入效果人看 | [06](modules/06-collection-anti-detect.md) |
| 07 | 抓取引擎 | ✅ | ✅ | 05,06 | `pytest tests/collection/test_field_extract.py tests/collection/test_engine.py -q` | 真跑 XHR(可选) | [07](modules/07-scrapers-engine.md) |
| 08 | 录制器+LLM | 🔄 | 🟡 | 05,07 | `pytest tests/collection/test_llm_mapper.py -q` | 人录制+anthropic key+真站点 | [08](modules/08-scrapers-recorder.md) |
| 09 | 验证码+调度 | ✅ | ✅ | 05,06 | `pytest tests/collection/test_rhythm.py -q` | captcha 真样本 | [09](modules/09-collection-captcha-scheduler.md) |
| 10 | 导出+图片磁盘 | ✅ | ✅ | 02 | `pytest tests/collection/test_csv_export.py -q` | — | [10](modules/10-collection-export-image.md) |
| 11 | 采集-集成 | 🔄 | 🟡 | 04,05,07,09,10 | `pytest tests/collection/test_integration.py tests/collection/test_sop_routes.py -q` | 真扫码登录（真平台账号） | [11](modules/11-collection-integration.md) |
| 12 | 测试 | 🔄 | ✅ | 各模块 | `pytest -q --cov=semilabs_hone --cov-fail-under=85` | — | [12](modules/12-tests.md) |
| 13 | 真实链路 E2E | ✅ | ✅ | 05,07,11 | `pytest tests/e2e -q`（无 Chrome 自动 skip） | — | [USER_SOP](USER_SOP.md) |

> **loop 自动交付候选（✅）**：01,02,03,03a,04,06,07,09,10,12 —— 共 10 个，loop 可全程自动标 done。
> **人工签收（🟡）**：05,05a,08,11 —— loop 写代码+单测，端到验留你。
> DM-12 为持续态，随各模块增量。

> **2026-07-28 设计 review 修复**：17 项问题（P0×4/P1×3/P2×8/P3×2）按四阶段修复，全部完成，详见 [FIX_PLAN.md](FIX_PLAN.md)（分支 fix/design-review-17，F1–F13 各自原子提交）。
>
> **2026-07-29 SOP 推演修复**：按用户操作 SOP 逐步推演 + 真实 E2E 验证，共 33 项（含 8 个 P0），全部完成，
> 详见 [USER_SOP.md](USER_SOP.md)；裁决已回写 `skim_design.md` §22。
> 原本的人工冒烟门（真 Chrome 跑一次 search flow）**已自动化**为 DM-13：`pytest tests/e2e -q`
> （真本地站点 + 真 Chrome + 真 worker 子进程 + 真 WebSocket）。DM-11 剩下的人工项只有
> “在真实平台（小红书）用自己手机扫码登录并跑一次真抓取”—— 这一步无法自动化。

## 依赖 DAG

```
01 core ──┬──> 02 models ──┬──> 04 web ──────────────────┐
          │                 ├──> 10 export/image ───────┐ │
          └──> 03 ipc ──────┤                           │ │
                            └──> 05 browser ──┬──> 06 anti ──┬──> 07 engine ──┬──> 08 recorder
                                              │             │                ├──> 09 captcha/sched
                                              │             │                │
                                              └─────────────┴────────────────┴──> 11 integration
                                                                                    ▲
                              04 web ────────────────────────────────────────────┘
                              10 export ─────────────────────────────────────────┘
```

**可并行批次**：A:01 → B:02,03 → C:04,05,10 → D:06 → E:07,09 → F:08 → G:11。

## 推荐推进顺序

- **loop 自动**（按关键路径）：01 → 03 → 05(代码半) → 06 → 07 → 09 → 10；02/04 穿插。
- **人工**（你做）：05 验收 → 08 录制 → 11 端到验。
- 08 是技术难点，单独一个完整会话（你人在）攻坚。

## 续接协议（新会话/loop 怎么接上）

1. 查本表状态，选下一个 ⬜ 且依赖全✅ 的模块（loop 只选 `可自动交付=✅`）。
2. 读三件套：PROJECT_CONTEXT.md → 目标 modules/NN-*.md → skim_design.md 对应章节。
3. 干完：勾模块 checklist → 跑 `scripts/loop_gate.sh` → 退出 0 才更新本表状态为 ✅ → commit + push。
4. 🟡 模块：loop 标 🔄（代码完成待人工验），不标 ✅。
5. 跨会话交接：在模块文档末尾"实施记录"写一句"下次从哪接"。

## 全局里程碑

- **M0 骨架** ✅（包树+CLI+config+规则+设计+本计划+约束 linter+loop_gate）
- **M1 core 三件套**：01/02/03 ✅，IPC echo op 端到端跑通。
- **M2 采集最小闭环**：05/06/07/08 ✅，能录制 XHS + 跑一条 search flow（08 需人工）。
- **M3 采集全功能**：09/10/11 ✅，扫码→抓取→存库→导出→验证码/恢复全通（11 需人工）。
- **M4 测试达标**：覆盖率核心 ≥85%（DM-12 自动门）。
- **M5（未来）**：analysis/production/operations 挂同一 core。

## PRD 对齐迭代里程碑（当前主线，详见 PRD_ALIGN_TASKS.md）

- **P0 安全/正确性** ✅：T01-T09 全完成（S1+S2）。
- **P1 数据模型** ✅：collection_* 表对齐 + WAL + upsert（S3）。
- **P2 采集能力** 🔄：跳过/边界/探针/节律(T20-T25 S4 完成)；可选验证码+知乎录制🟡(S5)。
- **P3 UI 行为** ⬜：保留 WS + 补 HTMX 徽章/dialog/展开/心跳/Toast。
- **P4 CSV 宽表** ⬜：左连接宽表 + 10 中文表头 + utf-8-sig。
- **P5 测试门禁** ⬜：PRD §8 BDD + 覆盖率 ≥85%。
- **P6 文档同步** ⬜：spec/design/context 与实现对齐。
- **P7 端到端** ❌：扫码+真抓取+导出（人工）。

> 分支 `feat/skim-prd-align`；裁决计划 `~/.claude/plans/semilabs-hone-docs-semilabs-hone-skim-s-snoopy-swan.md`。
