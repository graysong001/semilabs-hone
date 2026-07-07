"""全局配置 — 路径、端口、限额、安静时段、磁盘阈值、UA 策略。

设计依据见 docs/skim_design.md。运行时可被环境变量覆盖。
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SEMILABS_DATA_DIR", REPO_ROOT / "data"))

# 数据库 (各模块共享)
DB_PATH = DATA_DIR / "factory.db"
DB_URL = f"sqlite:///{DB_PATH}"

# IPC 文件队列根 (全厂任务总线)
IPC_ROOT = DATA_DIR / "ipc"
IPC_REQUESTS = IPC_ROOT / "requests"
IPC_RESULTS = IPC_ROOT / "results"
IPC_PROGRESS = IPC_ROOT / "progress"
IPC_CONTROL = IPC_ROOT / "control" / "cancel"

# Web 外壳
WEB_HOST = os.getenv("SEMILABS_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("SEMILABS_PORT", "8530"))

# 采集节律 (collection 模块)
QUIET_HOURS = (22, 7)  # 22:00-07:00 停跑
DAILY_LIMIT_PER_ACCOUNT = 200
NOTE_DELAY = (30, 90)          # 秒, 随机
KEYWORD_DELAY = (60, 180)      # 秒, 随机
WARMUP_PAGES = (2, 5)
WORKER_IDLE_TIMEOUT = 600      # 秒, 空闲自动退出

# 图片磁盘
IMAGE_DISK_WARN_GB = 30        # 超过报警 (WS warn + UI 角标, 不中断)
IMAGE_DISK_STOP_GB = None      # None=关; 设数值则超阈停下载

# UA 策略: real(本机真实 Chrome UA, 默认) | variety(远程库抓取)
UA_STRATEGY = os.getenv("SEMILABS_UA_STRATEGY", "real")
UA_REMOTE_URL = os.getenv("SEMILABS_UA_REMOTE_URL")  # variety 时必填
UA_POOL_TTL = 86400

# LLM 字段映射 (录制 + 失败兜底)
LLM_MODEL = os.getenv("SEMILABS_LLM_MODEL", "claude-haiku-4-5-20251001")

# Chrome
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT_RANGE = (9333, 9340)
