"""日志配置 — loguru 控制台 + data/logs/ 轮转。

调用: 应用启动时 `setup_logger()`，各模块通过 `get_logger(__name__)` 获取。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

_logger_initialized = False


def setup_logger() -> None:
    """初始化 loguru: 控制台 + data/logs/{date}.log 每日轮转。

    幂等: 重复调用不会重复添加 handler。
    日志目录按 config.DATA_DIR / "logs" 创建。
    """
    global _logger_initialized
    if _logger_initialized:
        return

    import config

    # 清除默认 handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 文件轮转: data/logs/ 目录, 每日轮转, 保留 30 天, UTF-8
    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_dir / "{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    _logger_initialized = True


def get_logger(name: str) -> "Logger":
    """获取带模块前缀的 logger 实例。

    Args:
        name: 通常为 __name__，如 "semilabs_hone.core.ipc.client"。

    Returns:
        绑定该 name 的 loguru logger。
    """
    return logger.bind(name=name)
