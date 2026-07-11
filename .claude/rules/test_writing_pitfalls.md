---
globs:
  - "tests/**/*.py"
  - "tests/**/conftest.py"
---
# 测试编写踩坑红线（S8 复盘）

> 写测试前先核 API，能砍掉 ~30% 的「写错→跑红→读输出→改」循环。以下每条都是真金白银交过学费的。

## 🔴 写测试前必做的 5 秒核验（避免 rework 税）

1. **grep 被测函数签名再写 mock**。先看 `async def f(...)` 还是 `def f`、参数认 `dict` 还是 `Locator` 对象、返回 coroutine 还是值。再动笔。
2. **session 生命周期**：SQLAlchemy `sess.close()` 后**不要**访问 ORM 属性——`commit()` 触发 `expire_on_commit`，close 后访问触发 `DetachedInstanceError`。payload 字段要在 close **前**捕获到局部变量。
3. **配置路径是 import 时静态值**：`config.IPC_RESULTS` 等在模块 import 时算定，`tmp_data_dir` 若只 monkeypatch `IPC_ROOT` **不会**重定向 `IPC_RESULTS`。要么 `importlib.reload(config)`（见 `tests/prd_bdd/conftest.py`），要么测试里显式 `monkeypatch.setattr(config, "IPC_RESULTS", tmp_dir)`。
4. **挂死 = 无限重试循环**：mock 让 `fetch_item` 永远抛同一异常，而重试 loop 不检查 `_await_resume` 返回值 → 死循环 → pytest 挂死。要么让 mock 第 2 次成功，要么 mock 返回 "stop" 出口。
5. **linter 用 `relative_to(ROOT)`**：`scripts/check_constraints.py` 对 repo 外的 tmp 文件会 `ValueError`。测 linter 规则时直接断言 `FORBIDDEN` 正则命中样本文本，别走 `_check_forbidden(path)` 的文件路径分支。

## 🧩 mock 模式：抄既有测试，别 inline 重写

- **优先复用**：本仓 `tests/collection/test_engine.py` 的 `_ScrollPage`/`MockPage`/`_make_scroll_collect_spec`、`test_risk_probes.py` 的 `_MockPage`、`test_solver.py` 的 `_async_counter`/`_MockPage`、`test_integration.py` 的 `_patch_handler_env`/`_make_task` 都是验证过的模式。`from tests.collection.test_engine import _ScrollPage` 可直接 import（`tests` 是包）。
- **AsyncMock vs MagicMock**：被 `await` 的方法用 `AsyncMock`（`return_value` 是值，`side_effect` 逐次返回值）。`MagicMock(return_value=coroutine)` 是坑——`await <function>` 会 TypeError。
- **lazy import 的依赖**（cv2/numpy/playwright/ddddocr/anthropic 未装）：用 `monkeypatch.setitem(sys.modules, "name", fake_mod)` 注入假模块，别尝试装真包。`fake_mod = types.ModuleType("name"); fake_mod.X = ...`。
- **拦截 `__import__`**：测 `from foo import bar` 的 ImportError fallback 分支时，`monkeypatch.setattr(builtins, "__import__", fake)`，但要**只对目标模块名 raise**，其余透传真实 import，否则连 `asyncio` 都 import 不了。

## ⚡ 覆盖率测量别跑全量

- 看单模块覆盖：`pytest --cov=semilabs_hone.path.to.mod tests/collection/test_xxx.py` 或跑完加 `grep <modname>`。
- 全量 `pytest --cov` 每次几十秒 + 长输出，迭代期别反复跑。阶段 checkpoint 跑一次即可。

## 🧭 探查不重复

- 派了 Explore agent 出模式后，**别再 inline 重读同一批测试文件**。要么信 agent 结论，要么自己读——不两路都做。
- agent 已给的 mock 代码片段，直接用，别重推导（重推导就是 S8 BDD 4.2 scroll mock XHR 超时回退 DOM 那次踩坑的根因）。

## 📐 收尾自检

- 写完每个测试文件，**先跑该文件一次**再进下一个。攒一堆再跑，错误叠加难定位。
- 闭合括号/keyword-only 参数这类语法错，写完 `python3 -c "import ast; ast.parse(open(f).read())"` 5 秒自检，省一次 pytest collection 失败。
