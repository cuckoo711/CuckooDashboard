#!/usr/bin/env python3
"""Cuckoo Dashboard native PyWebView launcher."""

from __future__ import annotations

import argparse
import ctypes
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import webview
from werkzeug.serving import make_server

from core.config import load_config
from core.logging_config import setup_logging
from core.monitor import load_target_monitor
from providers import get_providers

setup_logging(load_config())

logger = logging.getLogger(__name__)

APP_NAME = "CuckooDashboard"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "run_desktop.py"
PYTHONW = Path(__file__).resolve().parent.parent / "venv" / "Scripts" / "pythonw.exe"


def enable_dpi_awareness() -> None:
    """启用 Windows DPI 感知，避免缩放模糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def wait_for_server(host: str, port: int, timeout: float = 5.0) -> bool:
    """等待本地 Dashboard 服务器就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def install_autostart() -> None:
    """写入注册表实现开机自启。"""
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    cmd = f'"{PYTHONW}" "{SCRIPT_PATH}"'
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    winreg.CloseKey(key)
    print(f"[OK] 已注册开机自启: {APP_NAME}")
    print(f"     命令: {cmd}")


def uninstall_autostart() -> None:
    """删除注册表开机自启。"""
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print(f"[OK] 已取消开机自启: {APP_NAME}")
    except FileNotFoundError:
        print(f"[INFO] 未找到自启项: {APP_NAME}")


def _provider_status_summary() -> str:
    values = []
    for provider_id, provider in sorted(get_providers().items(), key=lambda item: item[0].casefold()):
        get_status = getattr(provider, "get_status", None)
        try:
            status = get_status() if callable(get_status) else {"status": "unknown"}
        except Exception as exc:
            status = {"status": "error", "error": str(exc)}
        values.append(f"{provider_id}:{status.get('status', 'unknown')}")
    return ", ".join(values) or "无已发现 Provider"


def main() -> None:
    parser = argparse.ArgumentParser(description="Cuckoo Dashboard Desktop App")
    parser.add_argument("--port", type=int, default=5000, help="服务器端口 (默认: 5000)")
    parser.add_argument("--dev", action="store_true", help="开发模式 (显示调试工具)")
    parser.add_argument("--install", action="store_true", help="注册开机自启后退出")
    parser.add_argument("--uninstall", action="store_true", help="取消开机自启后退出")
    args = parser.parse_args()

    if args.install:
        install_autostart()
        return
    if args.uninstall:
        uninstall_autostart()
        return

    # Import and construct the web app only for an actual desktop session.
    from app.factory import create_app
    from runtime.lifecycle import get_runtime

    enable_dpi_awareness()
    if not args.dev:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

    monitor = load_target_monitor()
    if monitor is None:
        print("[EXIT] 目标显示器未找到，自动退出")
        sys.exit(0)
    win_x, win_y = monitor["left"], monitor["top"]
    print(
        f"[OK] 找到目标显示器: {monitor['name']} "
        f"({monitor['width']}x{monitor['height']}) at ({win_x},{win_y})"
    )
    print("[INFO] Provider 状态: " + _provider_status_summary())

    app = create_app()
    app.debug = bool(args.dev)
    runtime = get_runtime(app)
    server = make_server("0.0.0.0", args.port, app, threaded=True)
    server_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="dashboard-http",
    )

    runtime.start()
    server_thread.start()
    try:
        print(f"Starting server on port {args.port}...")
        if not wait_for_server("127.0.0.1", args.port, timeout=10):
            print("[FAIL] Server failed to start within 10 seconds")
            sys.exit(1)

        url = f"http://127.0.0.1:{args.port}?_t={int(time.time())}"
        print(f"[OK] Server ready: {url}")
        window = webview.create_window(
            title="Usage Dashboard",
            url=url,
            x=win_x,
            y=win_y,
            width=monitor["width"],
            height=monitor["height"],
            resizable=True,
            frameless=True,
            text_select=True,
            on_top=True,
            fullscreen=True,
        )

        def on_loaded() -> None:
            window.evaluate_js(
                """
                document.addEventListener('keydown', function(e) {
                    if ((e.ctrlKey && e.key === 'r') || e.key === 'F5') {
                        e.preventDefault();
                        location.reload();
                    }
                });
                """
            )

        window.events.loaded += on_loaded
        print("[OK] Window created, opening...")
        webview.start(debug=args.dev)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        runtime.stop(timeout=5)


if __name__ == "__main__":
    main()
