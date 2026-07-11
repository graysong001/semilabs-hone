---
globs:
  - "tests/**/*.py"
---
# 测试编写踩坑红线

> 写测试前 5 秒核验，省 ~30% rework 税。

## 写前 5 秒核验

1. **grep 签名再写 mock**：async? 参数认 dict 还是对象? 返回 coroutine 还是值?
2. **session close 后别访问 ORM 属性**：commit→expire_on_commit→DetachedInstanceError。payload 字段 close 前捕获到局部。
3. **config 路径是 import 时静态值**：tmp_data_dir 不 reload config 时 `IPC_RESULTS` 等不重定向。`importlib.reload(config)` 或显式 monkeypatch。
4. **挂死=无限重试**：mock 别让被测永远抛同一异常且重试 loop 无出口。第 2 次成功或返回 stop。
5. **linter 用 `relative_to(ROOT)`**：测规则直接断言 `FORBIDDEN` 正则命中样本文本，别走文件路径分支。

## mock 复用

- **优先 import 既有 mock**：`tests/collection` 的 `_ScrollPage`/`_MockPage`/`_async_counter`/`_patch_handler_env`，别 inline 重写。
- **await 方法用 AsyncMock**。`MagicMock(return_value=coroutine)` 是坑。
- **未装依赖**（cv2/numpy/playwright/ddddocr/anthropic）：`monkeypatch.setitem(sys.modules, "name", fake_mod)`，别装真包。
- **拦 `__import__`**：只对目标名 raise，其余透传，否则 `asyncio` 都 import 不了。

## 收尾

- 看单模块覆盖：`pytest --cov=semilabs_hone.xxx tests/...`，迭代期别反复跑全量。
- 一文件一跑，别攒一堆。
- 复杂引号/括号：`python3 -c "import ast; ast.parse(open(f).read())"` 5 秒自检。
