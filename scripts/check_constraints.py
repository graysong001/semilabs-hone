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
]

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


def main() -> int:
    fails: list[str] = []
    for py in SRC.rglob("*.py"):
        txt = py.read_text(encoding="utf-8")
        _check_forbidden(py, txt, fails)
        _check_secrets(py, txt, fails)
    _check_cdp_args(fails)

    if fails:
        print("❌ 约束 linter 违例:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("✅ 约束 linter 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
