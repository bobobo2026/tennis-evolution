"""``python -m tennis_evolution.webapp --port 8787`` entry point."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import DEFAULT_PORT, serve_forever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tennis_evolution.webapp",
        description="本地 Web 版：长网球录像 → 带角标精彩合集（只监听本机）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）"
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="任务产物根目录（默认 <repo>/webapp-runs）",
    )
    parser.add_argument(
        "--demo-dir",
        type=Path,
        default=None,
        help="demo 产物目录（默认自动探测仓库上一级的 demo_run/）",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="启动后自动在浏览器中打开",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    serve_forever(
        host=args.host,
        port=args.port,
        runs_root=args.runs_root,
        demo_dir=args.demo_dir,
        open_browser=args.open_browser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
