# tests/ — 测试脚手架 (DM-12)

## 布局

- `conftest.py` — 公共 fixtures：`tmp_data_dir`(隔离 data/)、`db_session`(临时 SQLite，未建则 skip)、`load_fixture`、`fixtures_dir`。
- `fixtures/*.json` — XHS 响应样本（脱敏），供 field_extract / api_parser / llm_mapper 测试。
- `core/test_contract_core.py` — DM-01..04 接口契约测试。
- `collection/test_contract_collection.py` — DM-05..11 接口契约测试。

## 契约测试的作用

每个 `test_dmNN_*_contract` 用 `pytest.importorskip` 锁定该模块的**公开接口签名**（类/函数/字段存在）。模块未建时自动 **skip**（不破坏全量回归）；建好后必须通过——**接口漂移的守门员**。这是 `scripts/loop_gate.sh` 全量回归的一部分。

## 各 DM 落地时怎么加测试

每模块自带 `test_<模块>.py`（见 docs/modules/NN-*.md 任务清单），覆盖 testing.md 场景矩阵：正常/异常/边界/极值。命名 `test_<方法>_<场景>_<预期>`。

## 运行

```bash
pip install -e ".[dev]"          # 或最小: pip install pytest pytest-asyncio
pytest -q                         # 全量回归 (loop_gate 调用)
pytest tests/core/test_contract_core.py -q   # 单模块
pytest --cov=semilabs_hone --cov-fail-under=85   # 覆盖率门 (M4)
```
