# DEV_PLAN — 研发计划与进度跟踪

> 这是 semilabs-hone 的**单一进度真相源**。每次会话开始查此表选模块，结束更新此表。
> 模块切分依据 docs/skim_design.md §18 实施顺序，按依赖 DAG 拆成 12 个**单会话可完成**的独立单元。

---

## 状态图例

- ⬜ 未开始　🔄 进行中　✅ 完成　⛔ 阻塞（依赖未就绪）
- 依赖列：上游模块必须 ✅ 才能开工本模块。

## 模块进度表

| DM | 模块 | 状态 | 依赖 | spec 文档 | 关键产出 |
|----|------|------|------|-----------|----------|
| 01 | core 基座（config/utils/异常） | ⬜ | — | [01](modules/01-core-foundation.md) | config, logger, retry, SkimError 层级 |
| 02 | 数据模型（models/schemas） | ⬜ | 01 | [02](modules/02-data-models.md) | ORM 表 + Pydantic schemas + init_db |
| 03 | IPC 任务总线（core/ipc） | ⬜ | 01 | [03](modules/03-ipc-bus.md) | IPCRequest/Result/Progress + client + server |
| 04 | Web 外壳（core/ui） | ⬜ | 02,03 | [04](modules/04-web-shell.md) | create_app + WSManager + base/dashboard 模板 + manifest 注册 |
| 05 | 采集-浏览器进程（browser） | ⬜ | 01,03 | [05](modules/05-collection-browser.md) | launch_real_chrome + attach + worker_main + manifest |
| 06 | 采集-反检测（anti_detect） | ⬜ | 05 | [06](modules/06-collection-anti-detect.md) | stealth噪声 + human_behavior + fingerprint + ua_pool |
| 07 | 采集-抓取引擎运行时（scrapers/engine） | ⬜ | 05,06 | [07](modules/07-scrapers-engine.md) | PlatformSpec + GenericEngine + registry + field_extract |
| 08 | 采集-录制器+LLM映射（recorder/llm_mapper） | ⬜ | 05,07 | [08](modules/08-scrapers-recorder.md) | recorder + llm_mapper + 生成 XHS platform.yaml |
| 09 | 采集-验证码+调度（captcha/scheduler） | ⬜ | 05,06 | [09](modules/09-collection-captcha-scheduler.md) | slide/ocr/manual + rhythm + warmup |
| 10 | 采集-导出+图片磁盘（export/image_downloader） | ⬜ | 02 | [10](modules/10-collection-export-image.md) | csv_exporter + image_downloader(30GB报警) |
| 11 | 采集-集成（handlers+routes+templates） | ⬜ | 04,05,07,09,10 | [11](modules/11-collection-integration.md) | handlers(IPC op 分发) + routes + 模板 + 五阶段编排 |
| 12 | 测试（tests） | 🔄 | 各模块 | [12](modules/12-tests.md) | 跨模块测试计划 + fixtures（随各模块落地增量写） |

> DM-12（测试）是持续性的：每个 DM 落地时顺手写它的单测，DM-12 文档汇总计划与 fixtures。

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

**可并行的批次**（同批内互不依赖）：
- 批次 A：DM-01
- 批次 B：DM-02, DM-03（都只依赖 01）
- 批次 C：DM-04（依赖 02,03）, DM-05（依赖 01,03）, DM-10（依赖 02）
- 批次 D：DM-06（依赖 05）
- 批次 E：DM-07（依赖 05,06）, DM-09（依赖 05,06）
- 批次 F：DM-08（依赖 05,07）
- 批次 G：DM-11（依赖 04,05,07,09,10）—— 集成层，最后

## 推荐推进顺序（关键路径）

01 → 03 → 05 → 06 → 07 → 08 → 11（采集能跑通的最小链路）。
02/04/09/10 可穿插并行。08 是技术难点（CDP 录制 + Haiku 结构化输出），建议单独一个完整会话攻坚。

## 续接协议（新会话怎么接上）

1. **本会话在哪？** 查本表状态，选下一个 ⬜ 且依赖全 ✅ 的模块。
2. **读三件套**：PROJECT_CONTEXT.md → 目标 modules/NN-*.md → skim_design.md 对应章节。
3. **干完**：勾模块 checklist → 更新本表状态 + 模块文档"状态/实施记录" → commit + push。
4. **跨会话交接**：在模块文档末尾"实施记录"写一句"下次从哪接"。

## 全局里程碑

- **M0 骨架** ✅（已完成：包树 + CLI + config + 规则 + 设计文档 + 本计划）
- **M1 core 三件套**：DM-01..03 完成，IPC 可端到端跑一个 echo op。
- **M2 采集最小闭环**：DM-05..08 完成，能录制 XHS + 跑一条 search flow。
- **M3 采集全功能**：DM-09..11 完成，扫码登录→抓取→存库→导出→验证码/恢复全通。
- **M4 测试达标**：DM-12 覆盖率达标（核心 ≥85%，见 .claude/rules/testing.md）。
- **M5（未来）**：analysis / production / operations 各自 manifest 挂同一 core。
