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

# Worker 自动拉起 (L13): web 进程按需 Popen 采集浏览器 worker (CLAUDE.local.md)。
# 默认关——走 create_app() 的测试零副作用; cli `serve` 显式置 1 启用。
WORKER_AUTOSPAWN = os.getenv("SEMILABS_WORKER_AUTOSPAWN", "0") == "1"

# 采集节律 (collection 模块)
# SEMILABS_QUIET_HOURS: "2-8" 自定义时段 | "off" 关闭 (本机演练/E2E)
def _parse_quiet_hours(raw: str | None) -> tuple[int, int] | None:
    if raw is None:
        # 02:00-08:00 夜间静默 (PRD §2.2 场景; §4.5.1 写 22-07 与之矛盾, 以场景为准)
        return (2, 8)
    raw = raw.strip().lower()
    if raw in ("off", "none", ""):
        return None
    start, _, end = raw.partition("-")
    return (int(start), int(end))


def _parse_range(raw: str | None, default: tuple[int, int] | None) -> tuple[int, int] | None:
    """解析 "2-5" 形式的区间; "off"/"none" = None (关闭)。"""
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ("off", "none", ""):
        return None
    low, _, high = raw.partition("-")
    return (int(low), int(high or low))


QUIET_HOURS = _parse_quiet_hours(os.getenv("SEMILABS_QUIET_HOURS"))
DAILY_LIMIT_PER_ACCOUNT = 200
# 节律延迟的默认值就是安全基线; 环境变量可调仅为本机演练/自动测试,
# 调低后的封号风险由使用者自担。
NOTE_DELAY = _parse_range(os.getenv("SEMILABS_NOTE_DELAY"), (30, 90))       # 秒, 随机
KEYWORD_DELAY = _parse_range(os.getenv("SEMILABS_KEYWORD_DELAY"), (60, 180))  # 秒, 随机
WARMUP_PAGES = _parse_range(os.getenv("SEMILABS_WARMUP_PAGES"), (2, 5))  # None/off = 关闭预热
WARMUP_DWELL = (30, 90)        # 秒, 单个预热页停留
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


def ensure_data_dirs() -> None:
    """创建运行时数据目录（首次启动/自定义 SEMILABS_DATA_DIR 必需）。

    USER_SOP G34: 全新 data 目录下 SQLite 会直接报
    "unable to open database file" —— 数据目录必须有人负责建。
    幂等，可重复调用。路径只从 DATA_DIR / IPC_ROOT 推导，
    以便测试重定向这两个变量即可完全隔离。
    """
    for path in (
        DATA_DIR,
        DATA_DIR / "logs",
        IPC_ROOT / "requests",
        IPC_ROOT / "results",
        IPC_ROOT / "progress",
        IPC_ROOT / "control" / "cancel",
        DATA_DIR / "collection" / "images",
        DATA_DIR / "collection" / "profiles",
        DATA_DIR / "collection" / "exports",
        DATA_DIR / "collection" / "platforms",
    ):
        path.mkdir(parents=True, exist_ok=True)

# 验证码可选能力 (契约§5 / PRD §4.4): 默认关——命中即转人工 need_human。
# 仅 platform.yaml 声明 risk_tier=anonymous + captcha_policy=auto_then_manual 的
# 无登录(cargo 类)站点才走 slide/ocr 自动解, 失败 1 次转人工、不死循环。
CAPTCHA_DEFAULT_POLICY = "manual"              # manual | auto_then_manual
CAPTCHA_AUTO_SOLVE_PLATFORMS: list[str] = []   # 显式允许自动解的平台名白名单
