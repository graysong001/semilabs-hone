# DM-12 测试（tests）

> 状态：🔄 持续　|　依赖：各模块（随落地增量写）　|　设计依据：skim_design.md §20.12、.claude/rules/testing.md

## 范围
- `tests/core/`、`tests/collection/`
- `tests/fixtures/`（search/detail/comments 响应 JSON 样本）

## 目标
跨模块测试计划 + fixtures。核心业务/API 覆盖率 ≥85%（.claude/rules/testing.md）。每个 DM 落地时顺手写它的单测，本模块汇总计划与公共 fixtures。

## 测试矩阵（每核心函数覆盖，见 testing.md）
正常流程 / 异常处理（null/invalid/exception）/ 边界（empty/zero/max）/ 极值并发（timeout/concurrency/large）。

## 测试清单（随 DM 落地增量）

| 测试文件 | 归属 DM | 覆盖 |
|----------|---------|------|
| tests/core/test_retry.py | 01 | 异常层级 + fix_hint + 装饰器重试次数 |
| tests/core/test_models.py | 02 | 默认值 / upsert 二次更新 / task 生命周期 / resume 保留 last_note_index |
| tests/core/test_ipc.py | 03 | submit→result 端到端 / cancel / 原子写 / progress 流式 |
| tests/core/test_routes.py | 04 | TestClient 访问 / / 空库引导 / SkimError→JSON |
| tests/collection/test_cdp.py | 05 | find_free_port / port 冲突（mock subprocess） |
| tests/collection/test_human_behavior.py | 06 | 轨迹长度 / 延迟区间 |
| tests/collection/test_fingerprint.py | 06 | 固定性（同账号两次相同） |
| tests/collection/test_field_extract.py | 07 | JSONPath/CSS 取值 / 空/畸形 JSON |
| tests/collection/test_engine.py | 07 | mock page 跑 run_flow / 失败兜底 |
| tests/collection/test_llm_mapper.py | 08 | mock anthropic / map_group 结构化输出 / validate_map |
| tests/collection/test_rhythm.py | 09 | 安静时段 / 日限额 / 延迟区间 / captcha 阈值 |
| tests/collection/test_csv_export.py | 10 | AI 列头/top_comments / Excel ZIP 两 CSV / 空库 |
| tests/collection/test_api_parser.py | 07/08 | 用 fixtures 解析 XHS 响应 |

## fixtures
- `tests/fixtures/search_response.json`（XHS 搜索响应样本）
- `tests/fixtures/detail_response.json`（feed 响应样本）
- `tests/fixtures/comments_response.json`（评论响应样本）
- 用于 test_field_extract / test_api_parser / test_llm_mapper。

## 任务清单
- [x] tests/conftest.py：公共 fixture（tmp_data_dir / db_session(importorskip) / load_fixture / fixtures_dir）
- [x] 三份 fixtures JSON（XHS 搜索/feed/评论样本，脱敏）
- [x] 契约测试骨架：tests/core/test_contract_core.py（DM-01..04）+ tests/collection/test_contract_collection.py（DM-05..11），importorskip 未建则 skip
- [x] pyproject [tool.pytest.ini_options] + [tool.coverage]
- [x] tests/README.md
- [ ] 随各 DM 落地补对应 test_*.py（见上表）
- [ ] CI 本地命令：`pytest -q`（覆盖率 `pytest --cov=semilabs_hone --cov-fail-under=85`，核心模块）

## 验收（M4）
- `pytest -q` 全绿。
- 核心业务/API 覆盖率 ≥85%。

## 实施记录
- （待填）
