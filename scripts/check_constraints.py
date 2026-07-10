#!/usr/bin/env python3
"""约束 linter —— 把项目宪法的负面约束做成可执行门。

loop_gate 调用; 退出 0=通过, 1=违例。防止 agent 无人值守时偷写违宪代码。
对应 PROJECT_CONTEXT.md §3 硬约束。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "semilabs_hone"

# (正则, 说明) —— 命中即违例
FORBIDDEN: list[tuple[str, str]] = [
    (r"playwright\.launch\s*\(", "禁止 Playwright launch(); 必须 subprocess + connect_over_cdp"),
    (r"launch_persistent_context", "禁止 launch_persistent_context()"),
    (r"playwright[-_]stealth", "禁止 playwright-stealth (CDP 模式只注入噪声)"),
    (r"AutomationControlled", "禁止 --disable-blink-features=AutomationControlled"),
    (r"--enable-automation", "禁止 --enable-automation (automation flag)"),
    (r"--no-sandbox", "禁止 --no-sandbox"),
    (r"navigator\.webdriver\s*=", "禁止覆盖 navigator.webdriver (本就 undefined)"),
    (r"DROP\s+TABLE|TRUNCATE\s+", "禁止 DROP TABLE / TRUNCATE (见 .claude/rules/database.md)"),
    (r"while\s+is_captcha\s*:", "禁止 while is_captcha: 死循环重试刷新 (PRD §7.4 风控零容忍)"),
]

# while True 死循环: PRD §7.4 禁「while True/while is_captcha 死循环重试刷新」。
# 排除合法常驻 poll/receive 循环 (IPC server 主循环、WS receive 循环)。
WHILE_TRUE_RE = re.compile(r"while\s+True\s*:")
WHILE_TRUE_ALLOWLIST = {
    Path("semilabs_hone/core/ipc/server.py"),   # IPC 主 poll 循环
    Path("semilabs_hone/core/ipc/watchdog.py"), # 看门狗后台循环
    Path("semilabs_hone/core/ui/app.py"),       # WS receive 循环
}

# 疑似硬编码密钥: password/secret/api_key/token = "xxxx" (>=8 字符)
HARDCODED_SECRET = re.compile(
    r"(?:password|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
    re.IGNORECASE,
)

# browser/cdp.py 的 Chrome args 白名单
CDP_ARG_ALLOW = {"--remote-debugging-port", "--user-data-dir"}


def _check_forbidden(py: Path, txt: str, fails: list[str]) -> None:
    for pat, msg in FORBIDDEN:
        if re.search(pat, txt):
            fails.append(f"{py.relative_to(ROOT)}: {msg}  (/{pat}/)")


def _check_secrets(py: Path, txt: str, fails: list[str]) -> None:
    for m in HARDCODED_SECRET.finditer(txt):
        fails.append(f"{py.relative_to(ROOT)}: 疑似硬编码密钥 -> {m.group(0)[:50]}")


def _check_cdp_args(fails: list[str]) -> None:
    """browser/cdp.py 的 launch_real_chrome args 只允许白名单两参数。"""
    cdp = SRC / "modules" / "collection" / "browser" / "cdp.py"
    if not cdp.exists():
        return
    txt = cdp.read_text(encoding="utf-8")
    # 找所有 --xxx 参数
    for m in re.finditer(r'"(--[a-z0-9-]+)', txt):
        flag = m.group(1)
        # 允许 chrome 二进制路径里的 -- 不算; 仅检查 args 列表里的 flag
        if flag not in CDP_ARG_ALLOW:
            fails.append(f"cdp.py: 非白名单 Chrome 参数 {flag} (仅允许 {CDP_ARG_ALLOW})")


def _check_while_true(py: Path, txt: str, fails: list[str]) -> None:
    """禁 while True 死循环 (PRD §7.4)，allowlist 合法常驻循环。"""
    rel = py.relative_to(ROOT)
    if rel in WHILE_TRUE_ALLOWLIST:
        return
    if WHILE_TRUE_RE.search(txt):
        fails.append(
            f"{rel}: 禁止 while True: 死循环 (PRD §7.4)；"
            f"常驻 poll 循环需列入 WHILE_TRUE_ALLOWLIST"
        )


def _check_platform_yaml_captcha(fails: list[str]) -> None:
    """platform.yaml: account 站点不得设 captcha_policy=auto_then_manual。

    PRD §4.4 / 契约§5: account 站点(默认 risk_tier)命中验证码必须立即转
    人工(manual)；只有 anonymous + auto_then_manual 才允许 slide/ocr 自动解。
    """
    platforms = SRC / "modules" / "collection" / "scrapers" / "platforms"
    if not platforms.is_dir():
        return
    try:
        import yaml  # noqa: PyYaml 可选依赖
    except ImportError:
        return  # 无 pyyaml 时跳过该规则 (不阻塞)
    for yf in platforms.rglob("platform.yaml"):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        except Exception:
            # 坏 yaml 不归本规则管；跳过避免误伤
            continue
        if not isinstance(data, dict):
            continue
        risk_tier = data.get("risk_tier", "account")
        policy = data.get("captcha_policy", "manual")
        if risk_tier == "account" and policy == "auto_then_manual":
            fails.append(
                f"{yf.relative_to(ROOT)}: account 站点禁止 captcha_policy=auto_then_manual "
                f"(契约§5: account 命中即转人工 manual)"
            )


def main() -> int:
    fails: list[str] = []
    for py in SRC.rglob("*.py"):
        txt = py.read_text(encoding="utf-8")
        _check_forbidden(py, txt, fails)
        _check_secrets(py, txt, fails)
        _check_while_true(py, txt, fails)
    _check_cdp_args(fails)
    _check_platform_yaml_captcha(fails)

    if fails:
        print("❌ 约束 linter 违例:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("✅ 约束 linter 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
