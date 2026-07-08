"""错误处理层级与重试装饰器。

设计依据: docs/skim_design.md §11。
异常带 category + fix_hint(中文)，经 IPC 跨进程推送到 WS。
"""
from __future__ import annotations

from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)


class SkimError(Exception):
    """采集异常基类，携带分类标识与中文修复提示。"""

    def __init__(self, msg: str = "", category: str = "unknown", fix_hint: str = "") -> None:
        super().__init__(msg)
        self.category = category
        self.fix_hint = fix_hint


# ── 13 个子类 (按 skim_design.md §11) ──

class CaptchaError(SkimError):
    def __init__(self, msg: str = "验证码拦截") -> None:
        super().__init__(msg, category="captcha", fix_hint="请切换到 Chrome 窗口完成验证码，或联系人工处理。")


class RatelimitError(SkimError):
    def __init__(self, msg: str = "请求频率过高") -> None:
        super().__init__(msg, category="ratelimit", fix_hint="请求过快，已触发限流，等待冷却后自动恢复。")


class PageLoadError(SkimError):
    def __init__(self, msg: str = "页面加载失败") -> None:
        super().__init__(msg, category="page_load", fix_hint="页面加载超时或网络异常，将自动重试。")


class LoginError(SkimError):
    def __init__(self, msg: str = "登录失败") -> None:
        super().__init__(msg, category="login", fix_hint="登录验证未通过，请检查账号状态或重新扫码。")


class DataParseError(SkimError):
    def __init__(self, msg: str = "数据解析失败") -> None:
        super().__init__(msg, category="data_parse", fix_hint="响应格式异常，建议检查映射是否过期或重新录制。")


class SessionExpiredError(SkimError):
    def __init__(self, msg: str = "会话已过期") -> None:
        super().__init__(msg, category="session_expired", fix_hint="登录会话已失效，请重新登录。")


class AccountBannedError(SkimError):
    def __init__(self, msg: str = "账号已被封禁") -> None:
        super().__init__(msg, category="account_banned", fix_hint="该账号已被平台封禁，请更换账号或申诉。")


class QuietHoursError(SkimError):
    def __init__(self, msg: str = "节律安静时段") -> None:
        super().__init__(msg, category="quiet_hours", fix_hint="当前为安静时段(22:00-07:00)，任务暂停，将在恢复后继续。")


class DailyLimitError(SkimError):
    def __init__(self, msg: str = "日限额已满") -> None:
        super().__init__(msg, category="daily_limit", fix_hint="该账号今日抓取量已达上限，次日自动恢复。")


class BrowserClosedError(SkimError):
    def __init__(self, msg: str = "浏览器已关闭") -> None:
        super().__init__(msg, category="browser_closed", fix_hint="Chrome 浏览器意外关闭，请重启浏览器后恢复任务。")


class EmptyResultError(SkimError):
    def __init__(self, msg: str = "空结果") -> None:
        super().__init__(msg, category="empty_result", fix_hint="未抓取到有效数据，请检查关键词或平台状态。")


class PortConflictError(SkimError):
    def __init__(self, msg: str = "端口冲突") -> None:
        super().__init__(msg, category="port_conflict", fix_hint="CDP 端口被占用，将自动尝试下一个端口。")


class DiskFullError(SkimError):
    def __init__(self, msg: str = "磁盘空间不足") -> None:
        super().__init__(msg, category="disk_full", fix_hint="磁盘空间不足，请清理图片目录或调整磁盘阈值配置。")


# ── 重试装饰器 ──

# scraper_retry: 对 PageLoadError / TimeoutError 重试 3 次，指数退避 3s/6s/12s，上限 30s
scraper_retry = retry(
    retry=retry_if_exception_type((PageLoadError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=3, max=30),
    reraise=True,
)

# rate_limit_retry: 对 RatelimitError 重试 2 次，固定等待 300s
rate_limit_retry = retry(
    retry=retry_if_exception_type(RatelimitError),
    stop=stop_after_attempt(2),
    wait=wait_fixed(300),
    reraise=True,
)
