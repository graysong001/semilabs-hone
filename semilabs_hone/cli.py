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
    w.add_argument("--account", type=int, default=None, help="采集账号 ID (collection 模块必填)")

    sub.add_parser("version", help="打印版本")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "version":
        print(f"semilabs-hone {__version__}")
        return 0
    if args.cmd == "serve":
        # Wire the FastAPI shell via uvicorn (docs/skim_design.md §13). Enable
        # on-demand worker auto-spawn so the web process can pull up the browser
        # worker when a task/login IPC request is submitted (L13).
        import os
        import uvicorn
        os.environ.setdefault("SEMILABS_WORKER_AUTOSPAWN", "1")
        # config is read lazily by create_app() startup, so the env flip above
        # takes effect without a reload here.
        from semilabs_hone.core.ui.app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0
    if args.cmd == "worker":
        # Launch the collection browser worker: real Chrome + CDP + IPC serve
        # loop. (Currently collection-only; other modules would add a dispatch.)
        if args.module != "collection":
            print(f"worker: module '{args.module}' not supported (collection only)",
                  file=sys.stderr)
            return 1
        if args.account is None:
            print("worker: --account <id> is required for the collection module",
                  file=sys.stderr)
            return 1
        from semilabs_hone.modules.collection.browser.worker_main import main as worker_main
        return worker_main(["--account", str(args.account)])
    return 1
