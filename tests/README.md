# tests/ — 测试脚手架 (DM-12)

## 布局

- `conftest.py` — 公共 fixtures：`tmp_data_dir`(隔离 data/)、`db_session`(临时 SQLite，未建则 skip)、`load_fixture`、`fixtures_dir`；
  以及三个 autouse 安全网：`no_worker_launch`(默认禁止真拉 worker，E2E 用 `.original` 取回真函数)、
  `no_rhythm_sleep`(节律/预热停留置零)、`fresh_platform_registry`(平台缓存逐测试重置)。
- `fixtures/*.json` — XHS 响应样本（脱敏），供 field_extract / api_parser / llm_mapper 测试。
- `core/test_contract_core.py` — DM-01..04 接口契约测试。
- `collection/test_contract_collection.py` — DM-05..11 接口契约测试。
- `collection/test_pipeline.py` — 抓取管线（落库/评论排序/百分比/日限）真实 SQLite 测试。
- `collection/test_sop_routes.py` — 用户 SOP 的 HTTP 契约（真 app 工厂 + 真 IPC 文件）。
- `e2e/` — **真实链路**：stdlib HTTP 起真站点 + 产品自身的真 Chrome(CDP) + 真 worker 子进程 + 真 WebSocket。

## 真实测试原则（docs/USER_SOP.md §3）

- 浏览器层不用替身：`tests/e2e` 用 `launch_real_chrome + connect_over_cdp` 起真 Chrome，抓一个真本地站点
  （真 HTML + 真 `fetch()` + 滚动懒加载评论），断言真 SQLite 里的行、真 `/posts`、真 `/api/export`。
  没装 Chrome 的环境自动 skip（`config.CHROME_BIN` 不存在）。
- 单元/集成层可以用**显式的小替身**（例如 `test_pipeline.py` 里返回真 `ScrapedPost` 的 RecordedSiteEngine），
  但不允许用替身掩盖未实现的行为——真实行为一律由 `tests/e2e` 兜底验证。

## 契约测试的作用

每个 `test_dmNN_*_contract` 用 `pytest.importorskip` 锁定该模块的**公开接口签名**（类/函数/字段存在）。模块未建时自动 **skip**（不破坏全量回归）；建好后必须通过——**接口漂移的守门员**。这是 `scripts/loop_gate.sh` 全量回归的一部分。

## 各 DM 落地时怎么加测试

每模块自带 `test_<模块>.py`（见 docs/modules/NN-*.md 任务清单），覆盖 testing.md 场景矩阵：正常/异常/边界/极值。命名 `test_<方法>_<场景>_<预期>`。

## 运行

```bash
pip install -e ".[dev]"          # 或最小: pip install pytest pytest-asyncio
pytest -q                         # 全量回归 (loop_gate 调用, 含 e2e)
pytest tests/e2e -q               # 只跑真实链路 E2E (需要本机 Chrome)
pytest tests/core/test_contract_core.py -q   # 单模块
pytest --cov=semilabs_hone --cov-fail-under=85   # 覆盖率门 (M4)
```

E2E 会在本机真的打开若干 Chrome 窗口并访问 127.0.0.1 的测试站点，跑完自动关闭。
