# 主线合并计划：fix/design-review-17 → feat/skim-prd-align

> **本文档是本次合并的单一执行真相源。** 新会话开场读本文档即开工。
> 产生日期：2026-07-29。决策人：用户。方案经一轮自我 review 修正（E2E 提前、模板落位降级、cherry-pick 分级、spawner 合并裁决、legacy 列清理立项、vendor 保留）。

---

## 1. 背景与决策（不再重新讨论）

main (48939b5) 之后分出两条平行线，均未合回：

- **feat/skim-prd-align**（远程，+40 提交，S1-S9a）：发现 PRD（`docs/semilabs_hone_skim_sepc.md`，仅存在于该线）与骨架代码根本冲突后开的对齐线。强在数据模型（CollectionTask/UUID）、need_human 状态机、HTMX UI、CSV 宽表、BDD 16 场景、覆盖率 85% 门禁、心跳/watchdog/读后即焚/夜间长睡眠。
- **fix/design-review-17**（本地，+23 提交，F1-F13 + G1-G34）：旧骨架上的质量修复线。强在运行正确性——每个修复来自 SOP 推演或真实 E2E 暴露的缺陷（attach 重试、sort/scroll、DOM 兜底、单对象提取、账号路由、idle 退出、取消即时化、supervisor/tracker），且有真 Chrome 全栈 E2E 兜底。

**决策：以 feat/skim-prd-align 为主线，把 fix 线的修复与 E2E 资产移植过去。**
理由：数据模型分歧必须服从 PRD（产品真相源）；feat 线自带进度跟踪与门禁；fix 线价值以原子修复形式存在、可移植性好；feat 线价值是体系性的、不可拆。

**裁决优先级（执行中遇冲突不再纠结）**：
PRD（docs/semilabs_hone_skim_sepc.md） > skim_design.md §22 SOP 裁决 > 其他文档 > 代码现状。

**已裁决的三个冲突点**：
1. `QUIET_HOURS = (2, 8)`（PRD §2.2 场景为准，§4.5.1 的 22-07 是 PRD 内部矛盾），另加 `SEMILABS_QUIET_HOURS` 环境变量覆盖（默认即安全基线，E2E 需要 off）。
2. 写操作返回 HTMX 片段原地替换（G3），不用 303 重定向——feat 的 accounts.py 里 303 属回退，移植时修回。
3. stealth 服从 PRD zero-injection 红线（`NOISE_ONLY_SCRIPT=""` 空哨兵，inject_noise 为 no-op）；skim_design §4.4 噪声脚本作废。

---

## 2. 阶段 0：分支与工作区准备（先于一切）

```bash
cd /Users/xingjian/Documents/content-factory/semilabs-hone
# 0.1 处理 fix 线工作区未提交修改：
#     - vendor/ 保留（pico/htmx 本地化，合并后接进 base.html）
#     - base.html / style.css 的修改 stash 存档备查，可事后丢弃
git stash push -m "ui-skeleton-fix-backup" -- semilabs_hone/core/ui/templates/base.html semilabs_hone/core/ui/static/style.css
# 0.2 建工作分支
git fetch origin
git switch -c merge/skim-prd-align origin/feat/skim-prd-align
# 0.3 cherry-pick fix 线 4 个纯文档提交（代码提交一律不 pick，跨数据模型必冲突）
git cherry-pick d810008 e8e027c f8881a5 7e5051a
#     预期冲突：skim_design.md / DEV_PLAN.md。解法=保留 feat 版主线声明，把 fix 版
#     新增章节（§22 SOP 回写、§6.6 worker 契约、DM-13 行）追加进去，标注"与 PRD 冲突处以 PRD 为准"。
# 0.4 盘点 feat 线真实进度：读 docs/PRD_ALIGN_TASKS.md（DEV_PLAN 里程碑表滞后，
#     S6/S6b 已完成 P3、S7 已完成 P4、S8 已完成 P5，以 PRD_ALIGN_TASKS 为准），
#     确认 S9a 🔄 的剩余项，把结论补进本文档 §7 任务表。
# 0.5 把本文件 git add 进工作分支。
```

**阶段 0 盘点结论（0.4，2026-07-29 回写）**：
- feat 线真实进度（以 PRD_ALIGN_TASKS 为准）：S1-S4 ✅ / S5 🔄（T26 solver ✅，T27 知乎 hold 转 Sz）/ S6+S6b ✅（P3/P3.5）/ S7 ✅（P4 CSV 宽表+L03 旧列部分收口）/ S8 ✅（P5 BDD 16 场景+cov 85% 门）/ S9a 🔄 / S9 ⬜ / Sz ⏸。
- S9a 🔄 剩余项：① 真 Chrome 烟测（serve→登录→建任务→抓取→导出）② L02/L05 JS 运行时行为端到验 ③ L04 列表页 dialog 账号下拉查 DB ④ L11 `collection_items.url` NOT NULL（待 engine 补 url 采集）。其中 ①② 由本计划 P0d 的 E2E 移植覆盖；④ 归 Sz/S9 不在本次合并范围。
- 注意：feat 线 S7 已删 posts/comments 旧列（L03），但 task.py 的 legacy 列仍在（本计划 5.4 收口）。

**阶段 0 状态：✅**（0.1 stash `ui-skeleton-fix-backup` / 0.2 分支已建 / 0.3 四提交已 pick，冲突按“fix 完成态+feat 主线”解，supervisor.py/test_supervisor.py 不恢复（§4.4 裁决，能力 P0b 移植）/ 0.4 见上 / 0.5 已提交）

---

## 3. P0a：采集执行层正确性（不碰数据模型，先做）

每步对应 fix 线参照文件（`git show fix/design-review-17:<path>` 查看），手工移植：

| # | 任务 | fix 线参照 | 要点 |
|---|---|---|---|
| 3.1 ✅ | `browser/cdp.py`：attach 30s 重试（G32） | 同路径 | 真机首连必 ECONNREFUSED，重试不可少；重试耗尽后抛 feat 已有的 `CDPAttachError`（PRD §8.1 场景 1.2），不要抛 TimeoutError |
| 3.2 ✅ | `platforms/xiaohongshu/platform.yaml`：修回 sort 映射 + comments scroll 触发步（G16） | 同路径 | feat 删掉了属回退（不 sort 排序不生效、不 scroll 评论 XHR 永不触发）；保留 feat 的 `risk_tier`/`captcha_policy` 字段；顺带 login success_pattern "^/"→"^/$"（"^/" 匹配一切 path，登录必误判成功） |
| 3.3 ✅ | `scrapers/field_extract.py`：单对象响应回退（G25） | 同路径 | detail 型响应无列表时 map 作用于根对象；已并入 feat 版（保留 `parse_likes`/`title_fallback`）；`extract_dom` 尾部 `else [row]` 已修为 `else []`（G26 禁全 None 假行） |
| 3.4 ✅ | `scrapers/engine.py`：七项修回 | 同路径 | F7 platform 全组注入、F10/G26 DOM 兜底（仅 css:/xpath:）、G24 XHR 有界缓冲(200)+导航清缓冲+ensure_page 时武装、G22 urljoin 导航、F12 `_llm_model()` 读 config、G27 无凭证跳过 LLM、G29 fetch_item 多组合并。RiskProbeHit 探针与 `_scroll_collect` 已保留；顺带 F6：`ensure_page` 公开化(G10)+`_apply_fingerprint_once`（fingerprint.py 采 fix 版：per-account 随机抽取+CDP Emulation apply_to_page，废弃 init-script 注入，服从零注入红线） |
| 3.5 ✅ | `scheduler/rhythm.py`：补回 `warmup_dwell` | 同路径 | 已补；`is_quiet_hours`/`sleep_until_wakeup` 保持不动，仅加 `QUIET_HOURS=None`(off) 容错；config.py 同步合并环境变量覆盖（默认 (2,8) 不变，裁决 1）+ `ensure_data_dirs`(G34) |

**验收门：✅**（2026-07-29：门内 117 用例全绿；防回归扩跑 tests/collection + tests/prd_bdd 共 447 全绿；linter 绿。提交 0e1bbaf/a6c8760/65e893e）

---

## 4. P0b：IPC 与 worker 生命周期

| # | 任务 | fix 线参照 | 要点 |
|---|---|---|---|
| 4.1 ✅ | `ipc/server.py` 合并 | 同路径 | feat 基础（burn/坏 JSON/control/心跳/need_human）上已移入：F1 路由（新增 `_peek_request` 先判路由后 burn，避免烧掉别人的请求）、F11 idle 退出+孤儿 GC、F3 `_RequestCancelled` 取消即时化、F9 ws_events 透传 |
| 4.2 ✅ | `ipc/client.py`：result consume-on-read（F11） | 同路径 | 已并入（poll_heartbeat 保留） |
| 4.3 ✅ | `browser/worker_main.py` 合并 | 同路径 | signal handlers、proc.terminate() 兜底（先 close 后 terminate）、`__main__` 块(G31)、account 加载+`set_worker_resources(ctx, account)`（handlers 侧已升级兼容签名，`set_worker_ctx` 保留为别名）；serve_worker 传 account_id |
| 4.4 ✅ | supervisor/tracker 能力并入 spawner/watchdog | `core/ipc/supervisor.py`、`core/ui/tracker.py` | 已按裁决保留 feat 入口：worker_spawner 得到 per-(module,account) 注册表+manifest WORKER_ENTRY+日志重定向(G33)+ensure_worker/is_alive/stop_worker/shutdown_all；run_progress_relay 得到 G4 类型化事件+终态广播+result consume-on-read；watchdog 事件补 category=BrowserClosedError（死 worker 广播经 watchdog 兜底，非 per-request tracker） |
| 4.5 ✅ | `core/ui/app.py` 合并 | 同路径 | startup（watchdog+relay）保留；shutdown 已接 `worker_spawner.shutdown_all()`（G12） |

**验收门：✅**（2026-07-29：test_ipc+test_worker_spawner+relay/wiring/s9a 共 87 绿；fix 线 test_supervisor 已适配并入 test_worker_spawner（含 G31 模块可执行回归）；tracker 测试由 relay/watchdog 测试覆盖不另移植；全量 636 绿。提交 d8a8b03/3d0b4bd）

---

## 5. P0c：handlers 与路由（PRD 模型切换主战场）

| # | 任务 | 要点 |
|---|---|---|
| 5.1 ✅ | `handlers.py`：fix 采集管线移植到 feat 状态机骨架 | 移植（参照 fix 线）：带 sort 的 `_search_keyword`、`_fetch_detail`、`_fetch_top_comments`、图片下载、`_upsert_post` 完整字段（published_at/json 序列化）、`_upsert_comments`、断点去重（`_load_scraped_platform_ids`）、配额自增+跨天归零（G9）、三级会话恢复。**全部直接用 PRD 列名**（actual_count/error_msg 等），不再写 legacy 列。保留 feat 的 `_promote_to_running`/`_set_task_need_human`/`_await_resume`/`_night_sleep_if_quiet` 与 DailyLimitError→paused 接线（提交 4f1437e） |
| 5.2 ✅ | `routes/accounts.py`：G2/G3/F6 移植 | G2 平台取账号的（修回 feat 的 xiaohongshu 硬编码）；G3 写操作返回 `_accounts_table` 片段（集中式 `core/ui/templates/_accounts_table.html`，页面 `{% include %}`）；F6 建号即分配指纹+profile；保留 feat 的 worker_spawner 调用（不用 fix 的 tracker，P0b 裁决已并入 ws relay）；login/validate/import-cookies 加 404+fix_hint + Cookie JSON 校验 |
| 5.3 ✅ | `routes/tasks.py`：G8 前置校验移植 | 平台/账号存在/同平台/已登录/关键词非空校验，4xx+fix_hint；适配 PRD TaskCreate（target_value 进 DB，keywords 进 IPC payload）；F3 request_id 持久化、G6 progress 降级端点保留。**单任务锁裁决：PRD §2.2 场景1 「排队执行」优先**——已 running/pending 时新任务 pending 排队（不 409 拒绝），与 feat 原行为一致；resume Path B 补 task_type/target_value/keywords 进 payload + 写回新 request_id |
| 5.4 ✅ | `models/task.py`：legacy 列删除（feat S3 过渡收官） | 确认 5.1-5.3 无引用后，删 legacy 列（account_id 裸列/max_posts_per_keyword/posts_scraped/last_note_index/sort_type/download_images/collect_comments/error_message/error_category/started_at/completed_at）。**破坏性变更**：本地工具不写迁移脚本——引导用户「先 /api/export 导出备份 CSV，再删 data/factory.db 重启重建」。watchdog/task_detail.html/contract 测试均已切到 PRD 列名 |

**验收门 ✅**：`pytest tests -p no:cov --ignore=tests/e2e` 634 全绿；BDD 16 场景全绿；`check_constraints.py` 通过。

---

## 6. P0d：E2E 适配与竣工验收（原方案误放 P1，review 后提前）

| # | 任务 | 要点 |
|---|---|---|
| 6.1 ✅ | `tests/e2e/` 全目录适配 PRD 模型 | 4 文件已移植并适配：Post→CollectionItem / Comment→CollectionComment / content→content_text / likes→metrics_json(unpack_metrics) / rank→like_count 排序 / progress 端点 `{ok,message,data}` 嵌套结构 / `/posts/{uuid}`；in-process 版 progress 断言改 404 降级（无 IPC request_id 的任务无 progress 文件，G6 契约） |
| 6.2 ✅ | 真 Chrome 全栈 E2E 跑通 | 9/9 绿（31s）。E2E 暴露并已修 4 缺陷：① ScrapedPost/ScrapedComment 缺 `platform` 字段（F7 注入被 pydantic 静默丢弃→存库落回 xiaohongshu）+ `published_at` 严格 str 型遇 epoch int 整组 ValidationError→title/content 全空（采 fix 版 schema）② cancel 路由用 `task-{task_id}` 写哨兵而 worker 按 request_id 查——取消永远到不了 worker（F3）③ warmup 采 fix 版（urls 可注入+WARMUP_PAGES=None 关闭+dwell 走 rhythm；feat 版硬编码 30-90s 真睡且不可关）④ conftest 的 QR_LOGIN_TIMEOUT=0.05s 单测捷径由 E2E fixture 覆盖回真实值 |
| 6.3 ✅ | 全量回归 | `bash scripts/loop_gate.sh` 绿：约束 linter + 645 用例（含 e2e 9）+ 覆盖率 85.10%≥85%。顺手修二缺陷：test_routes_s9a 本地 fixture reload(config) 没 setenv——progress 文件写进真实 data/ipc、db teardown drop 真实库（已修+补 launchagent/cli-entry 覆盖缺口测试）；loop_gate.sh 覆盖率 FAIL 后仍打 ✅（已显式拦截退出码） |

**P0 完成定义：✅**（2026-07-29：loop_gate 绿 + E2E 9/9 绿 + BDD 16 场景绿 + legacy 列已删。提交 9f4c407/b4ea63d/bce66ad）

---

## 7. P1：UI 合并（内容以 feat 为准，补 fix 交互）

| # | 任务 | 要点 |
|---|---|---|
| 7.1 ✅ | `app.js` 合并 | feat 版为底保留（exportCsv/HTMX 错误 Toast/showToast/`msg.duration`），补 fix 三件套：`ws:message` DOM 事件派发（task_detail 的监听此前是死代码）、`data-progress-for` 进度选择器（替换无模板使用的 `progress-<id>` 死代码+重复的 `#task-log` 追加）、`session_status` case。提交 6e399ef |
| 7.2 ✅ | `base.html` 合并 | feat 版为底；CDN 的 pico/htmx 换成 **vendor 本地引用**（vendor/ 已提交，本地工具不依赖外网）；导航用 fix 的 manifest NAV 循环（配套：manifest.py 加 NAV 三条 + app.py `_discover_modules` 带 `nav`；旧 `/{mid}` 链接无对应路由会 404）；保留 heartbeat 指示器（/api/heartbeat 端点在）。T30 断言改 vendor 路径。提交 7f40cde |
| 7.3 ✅ | `style.css` 合并 | feat 195 行版为底，从 stash 447 行工作区版补缺（以模板实际引用的 class 逐个核对，不整段搬、不引 `--hone-*` 变量体系）：补 .logo/.badge.inactive/.badge.suspended/.progress-container/.progress-fill/#progress-text/.log-container/.task-log/.action-buttons/.notification-area/.alert-*/.task-result/.filter-form/.post-content/.comment-item/.pagination/.htmx-request。提交 39b1de3 |
| 7.4 | （可选）模板落位模块目录（F8） | PRD 无要求、feat 集中式已接线，**默认不做**；仅当后续加站/加模块感到集中式混乱时再做 |

**验证**：路由回归 78 绿；loop_gate 全绿（645 用例 + 覆盖率 85.08%）；TestClient 冒烟 `/ /accounts /tasks /posts` + 4 个静态资产全 200、NAV 循环渲染正确。

**验收门**：人工走查 SOP 一遍（账号添加→登录→建任务→进度→导出 CSV→异常按钮），对照 docs/USER_SOP.md。

---

## 7b. v2 暗色 UI 全量移植（feat/ui-v2-dark，off main）

v2 分支后端停在旧点（缺 P0 修复/schema 硬化/E2E），直接 merge 有丢修复风险 → **手工移植**：UI 资产 `git checkout origin/feat/ui-redesign-v2 -- <path>`，Python 路由/模型/handler 以 main 为底手工叠加 v2 新增。Tailwind Play CDN + Google Fonts 全部 vendor 本地化，离线可用。

| # | Stage | 要点 | 提交 |
|---|---|---|---|
| 7b.1 ✅ | 基座 | vendor（tailwind.js + 5 族字体 woff2 + fonts.css）；base/_sidebar/style.css 取 v2；app.js 调和（v2 为底 + 保留 `data-progress-for`/`session_status`） | 799009d |
| 7b.2 ✅ | dashboard 任务大厅 | dashboard.py 统计卡/分页/badge_map；6 模板取 v2 | 6c990ea |
| 7b.3 ✅ | 任务路由重构 | `/tasks` 302→`/`；删 `/tasks/new`、`/tasks/{id}` 页面及 5 模板；新增 pause/DELETE/activate-browser 端点；`/row` 渲染 partials/_task_row；保留 cancel/resume 修复 | 9c500d6 |
| 7b.4 ✅ | posts 数据资产 | posts.html/_comments 取 v2；post_detail v2 未重写 → 轻量暗色化（数据绑定不变）；posts.py 补 task_id 过滤/active_page='data' | 7bcf11c |
| 7b.5 ✅ | accounts 三标识 | nickname→remark(NOT NULL) + platform_user_id/platform_nickname + UNIQUE(platform,platform_user_id)；保留 phone/profile_dir/daily_count_date；v2 七端点叠加；handlers 身份提取（identity_api/identity_map）+ cookie_import 冲突 LoginError；3 模板取 v2 | 9155751 |
| 7b.6 ✅ | 测试适配 | E2E remark 遗漏修复；s9a/bdd_04_nav 14 绿确认；E2E 仅依赖 API 端点不依赖被删页面 | 9dd31fe |
| 7b.7 ✅ | 验收 | loop_gate 全绿（覆盖率 84%→85%，TestStage7CoveragePatch 14 测试补分支）；TestClient 冒烟 `/ /accounts /posts` + vendor 200 + 任务生命周期 | 738db74 |

**计划外适配（v2 不可直搬的三处）**：① delete 守卫——PRD 无 task→account FK，v2 按 account_id 列查 running 不可搬 → 平台化守卫（同平台 pending/running → 409）；② create 保留 main F6 指纹分配（v2 无）；③ v2 import-cookies 冲突透传在文件 IPC 下是死代码 → 冲突只走 WS `cookie_import_conflict` 事件。

**遗留（用户操作）**：accounts schema 变更（remark NOT NULL + 新列 + UNIQUE）无迁移 → 先 `/api/export` 备份，删 `data/factory.db` 重启重建，再人工走查 SOP（三标识登录 → 任务大厅建任务 → 风控弹窗 → 进度 → 导出）。

---

## 8. P2：收尾

1. `docs/DEV_PLAN.md`：更新主线声明（合并完成，fix 线 F/G 已全部移植或归档）。
2. `docs/skim_design.md`：§22/§6.6 保留，抬头标注「历史裁决，冲突处以 PRD 为准」；开头对 `skim.spec` 的引用改为 PRD 文档（feat 线已删 skim.spec，跟随不恢复）。
3. `docs/USER_SOP.md`/`docs/FIX_PLAN.md`：保留作裁决档案，抬头标注状态。
4. 合回 main：`git switch main && git merge --no-ff merge/skim-prd-align`，推远程；PRD_ALIGN_TASKS.md 标记合并任务完成。
5. 归档分支：`fix/design-review-17` 本地保留不删（历史参照），`feat/skim-prd-align` 合并后可删远程分支。

---

## 9. 风险清单

- **E2E fixture 改写面**：`_active_account`/`_wait_for_task` 按 int id 查询，UUID 化后全部要改；先改 conftest 再跑。
- **handlers.py 是最大单点**（1155 行 diff）：5.1 建议拆 2-3 个原子提交（管线移植 → PRD 列名切换 → legacy 清理）。
- **删 legacy 列后老库打不开**：启动时 `create_all` 不会重建已有表，老 data/factory.db 含旧列不报错但 ORM 查询会炸——必须提醒用户删库（见 5.4）。
- **feat 线 `extract_dom` 的 `else [row]`**（空行也返回）：与 G26「禁止全 None 假行入库」冲突，3.3 时复核修正。
- **风格纪律**：移植以 fix 线文件为参照但**不整文件覆盖** feat 版，逐 hunk 合并；每阶段验收门全绿才进下一阶段。

---

## 10. 新会话 kickoff（可直接粘贴）

> 读 docs/MERGE_PLAN.md 并从此执行主线合并。工作分支 merge/skim-prd-align（基于 origin/feat/skim-prd-align）。进度真相源：本文档任务表 + docs/PRD_ALIGN_TASKS.md。裁决优先级：PRD（docs/semilabs_hone_skim_sepc.md）> skim_design.md §22 > 其他。fix 线参照文件用 `git show fix/design-review-17:<path>` 查看。从 §2 阶段 0 开始，逐阶段执行，每阶段验收门全绿才进下一阶段；完成情况直接回写本文档任务表（🔄/✅）。
