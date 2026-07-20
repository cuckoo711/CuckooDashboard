#!/usr/bin/env python3
"""Cuckoo Dashboard web entry point and compatibility facade."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from core.config import load_config
from core.logging_config import setup_logging

# Configure logging before importing the application graph and Provider modules.
setup_logging(load_config())

from app.factory import create_app  # noqa: E402
from runtime.lifecycle import get_runtime  # noqa: E402

logger = logging.getLogger("cuckoo.dashboard")

# Compatibility export for existing launchers/tests. Creating the app is side-effect free:
# runtime threads are started explicitly by main() or start_background_threads_once().
app = create_app()


def start_background_threads_once() -> bool:
    """Compatibility wrapper for the unified application runtime."""
    return get_runtime(app).start()


def stop_background_threads(timeout: float = 5) -> None:
    """Stop the compatibility app's managed runtime."""
    get_runtime(app).stop(timeout=timeout)


def _dev_extra_files() -> list[str]:
    static_dir = Path(__file__).resolve().parent / "static"
    return [
        str(path)
        for path in static_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Cuckoo Dashboard")
    parser.add_argument("--port", "-p", type=int, default=5000, help="端口号 (默认 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--open", "-o", action="store_true", help="自动打开浏览器")
    parser.add_argument("--dev", action="store_true", help="开发模式：启用 Flask debug/reloader")
    args = parser.parse_args()

    if args.open:
        import webbrowser

        url = f"http://{args.host}:{args.port}"
        logger.info("正在打开浏览器: %s", url)
        webbrowser.open(url)

    logger.info("Cuckoo Dashboard 启动中...")
    logger.info("访问地址: http://%s:%s", args.host, args.port)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.info("[security] 当前不是仅本机监听；POST 接口会要求同源或 X-Dashboard-Token")
    logger.info("按 Ctrl+C 停止服务器")

    runtime = get_runtime(app)
    actual_server_process = not args.dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if actual_server_process:
        runtime.start()

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.dev,
            use_reloader=args.dev,
            extra_files=_dev_extra_files() if args.dev else None,
        )
    finally:
        if actual_server_process:
            runtime.stop()


if __name__ == "__main__":
    main()
