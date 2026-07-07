# DM-01 core 基座（config / utils / 异常层级）

> 状态：⬜ 未开始　|　依赖：—　|　设计依据：skim_design.md §2、§11、§13.3、config.py

## 范围
- `config.py`（已存在 stub，本模块补全/校准）
- `semilabs_hone/core/utils/logger.py`
- `semilabs_hone/core/utils/retry.py`

## 目标
全厂复用的配置、日志、异常层级与重试装饰器。所有其他 DM 依赖此模块，**接口必须最先稳定**。

## 产出接口契约

### `core/utils/retry.py`
```python
class SkimError(Exception):
    category: str        # 异常分类标识
    fix_hint: str        # 中文修复提示, 推送给用户

# 子类 (skim_design.md §11)
CaptchaError, RatelimitError, PageLoadError, LoginError, DataParseError,
SessionExpiredError, AccountBannedError, QuietHoursError, DailyLimitError,
BrowserClosedError, EmptyResultError, PortConflictError, DiskFullError

# tenacity 装饰器
scraper_retry      # PageLoadError/Timeout: 3 次指数 3/6/12s 上限 30s
rate_limit_retry   # RatelimitError: 2 次固定 300s
# 不重试: Captcha/Login/AccountBanned/SessionExpired
```

### `core/utils/logger.py`
```python
def setup_logger() -> None           # loguru 配置, 落 data/logs/
def get_logger(name: str) -> Logger  # 模块取 logger
```

### `config.py`（已存，校准下列键）
路径 `REPO_ROOT/DATA_DIR/DB_PATH/IPC_*`、`WEB_HOST/PORT`、`QUIET_HOURS`、`DAILY_LIMIT_PER_ACCOUNT`、`NOTE_DELAY/KEYWORD_DELAY`、`IMAGE_DISK_WARN_GB=30`、`IMAGE_DISK_STOP_GB=None`、`UA_STRATEGY="real"`、`LLM_MODEL="claude-haiku-4-5-20251001"`、`CHROME_BIN`、`CDP_PORT_RANGE=(9333,9340)`。

## 关键约束
- 异常必须带 `category` + `fix_hint`（中文，WS 推送给用户）。
- 配置敏感值（LLM API key 等）只从环境变量读，不硬编码。
- `config.py` 在 repo 根，import 方式 `import config`（repo 根在 sys.path 时）。若需包内引用，用 `from semilabs_hone import ...` 拿别的，config 仍走 `import config`。

## 任务清单
- [ ] `retry.py`：SkimError 基类 + 13 子类，每个带默认 category/fix_hint
- [ ] `retry.py`：`scraper_retry` / `rate_limit_retry` 装饰器（tenacity，只重试指定异常）
- [ ] `logger.py`：setup_logger（控制台 + data/logs/<date>.log 轮转）+ get_logger
- [ ] `config.py`：校准所有键，环境变量覆盖优先
- [ ] 单测 `tests/core/test_retry.py`：异常层级 + fix_hint 存在 + 装饰器重试次数

## 验收
- `python -c "from semilabs_hone.core.utils.retry import SkimError, CaptchaError; print(CaptchaError('x').fix_hint)"` 输出中文提示。
- `pytest tests/core/test_retry.py` 绿。

## 实施记录
- （待填）
