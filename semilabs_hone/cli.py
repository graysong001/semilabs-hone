"""semilabs-hone CLI 入口。

Usage:
    python -m semilabs_hone serve [--host 127.0.0.1] [--port 8530]
    python -m semilabs_hone worker --module collection
    python -m semilabs_hone version
"""
import argparse
import sys

from semilabs_hone import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="semilabs-hone",
        description="内容工厂 — 多平台内容素材采集与分析系统",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="启动 Web UI (默认)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8530)

    w = sub.add_parser("worker", help="启动模块 worker 进程")
    w.add_argument("--module", default="collection", help="模块名 (collection/analysis/...)")

    sub.add_parser("version", help="打印版本")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "version":
        print(f"semilabs-hone {__version__}")
        return 0
    if args.cmd == "serve":
        # TODO: init_db + setup_logger + uvicorn (docs/skim_design.md §13)
        print(f"[TODO] serve on {args.host}:{args.port} — 见 docs/skim_design.md §13", file=sys.stderr)
        return 1
    if args.cmd == "worker":
        # TODO: 启动对应模块 worker, 跑 core.ipc.server 主循环 (docs/skim_design.md §6)
        print(f"[TODO] worker --module {args.module} — 见 docs/skim_design.md §6", file=sys.stderr)
        return 1
    return 1
