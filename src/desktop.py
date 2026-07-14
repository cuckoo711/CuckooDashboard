#!/usr/bin/env python3
"""
MiMo Usage Desktop App
原生桌面应用，无需打开浏览器。

使用方式:
    python desktop.py              # 默认端口 5000
    python desktop.py --port 8080  # 指定端口
    python desktop.py --dev        # 开发模式（显示控制台）
    python desktop.py --install    # 注册开机自启
    python desktop.py --uninstall  # 取消开机自启
"""

import argparse
import ctypes
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import webview

from core.config import load_config
from core.logging_config import setup_logging

# 在 dashboard import 之前初始化日志（desktop 模式可能需要 console: false）
setup_logging(load_config())

# 导入Flask应用
from dashboard import app, start_background_threads_once
from core.monitor import load_target_monitor
from providers.mimo.api import get_mimo_api

logger = logging.getLogger(__name__)

APP_NAME = "MiMoUsageDashboard"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "run_desktop.py"
PYTHONW = Path(__file__).resolve().parent.parent / "venv" / "Scripts" / "pythonw.exe"


def enable_dpi_awareness():
    """启用 Windows DPI 感知，避免缩放模糊"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI Aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def wait_for_server(host: str, port: int, timeout: float = 5.0) -> bool:
    """等待Flask服务器就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def start_flask(port: int, debug: bool = False):
    """在后台线程启动Flask服务器"""
    app.run(host='0.0.0.0', port=port, debug=debug, use_reloader=False)


def install_autostart():
    """写入注册表实现开机自启"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    cmd = f'"{PYTHONW}" "{SCRIPT_PATH}"'
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    winreg.CloseKey(key)
    print(f"[OK] 已注册开机自启: {APP_NAME}")
    print(f"     命令: {cmd}")


def uninstall_autostart():
    """删除注册表开机自启"""
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print(f"[OK] 已取消开机自启: {APP_NAME}")
    except FileNotFoundError:
        print(f"[INFO] 未找到自启项: {APP_NAME}")


def main():
    parser = argparse.ArgumentParser(description='MiMo Usage Desktop App')
    parser.add_argument('--port', type=int, default=5000, help='服务器端口 (默认: 5000)')
    parser.add_argument('--dev', action='store_true', help='开发模式 (显示Flask日志)')
    parser.add_argument('--install', action='store_true', help='注册开机自启后退出')
    parser.add_argument('--uninstall', action='store_true', help='取消开机自启后退出')
    args = parser.parse_args()

    if args.install:
        install_autostart()
        return
    if args.uninstall:
        uninstall_autostart()
        return

    # Windows DPI 设置
    enable_dpi_awareness()

    # 非开发模式下静默Flask日志
    if not args.dev:
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

    # 检测目标显示器
    monitor = load_target_monitor()
    if monitor is None:
        print("[EXIT] 目标显示器未找到，自动退出")
        sys.exit(0)
    win_x, win_y = monitor['left'], monitor['top']
    print(f"[OK] 找到目标显示器: {monitor['name']} ({monitor['width']}x{monitor['height']}) at ({win_x},{win_y})")

    # MiMo Cookie 仅影响 MiMo 卡片，不应阻塞桌面看板启动
    try:
        api = get_mimo_api()
        if api:
            print("[OK] Cookie detected")
        else:
            print("[WARN] MiMo Cookie missing or expired; dashboard will start with MiMo unavailable")
    except Exception as e:
        print(f"[WARN] MiMo check failed: {e}; dashboard will continue")

    # 启动 WebSocket 广播线程
    start_background_threads_once()

    # 在后台线程启动Flask
    flask_thread = threading.Thread(
        target=start_flask,
        args=(args.port, args.dev),
        daemon=True
    )
    flask_thread.start()

    # 等待Flask真正就绪
    print(f"Starting server on port {args.port}...")
    if not wait_for_server('127.0.0.1', args.port, timeout=10):
        print("[FAIL] Server failed to start within 10 seconds")
        sys.exit(1)

    # 创建原生窗口（加时间戳避免缓存旧页面）
    url = f"http://127.0.0.1:{args.port}?_t={int(time.time())}"
    print(f"[OK] Server ready: {url}")

    window = webview.create_window(
        title='MiMo Usage Dashboard',
        url=url,
        x=win_x,
        y=win_y,
        width=monitor['width'],
        height=monitor['height'],
        resizable=True,
        frameless=True,
        text_select=True,
        on_top=True,
        fullscreen=True,
    )

    def on_loaded():
        """页面加载完成后注入快捷键：Ctrl+R / F5 刷新"""
        window.evaluate_js("""
            document.addEventListener('keydown', function(e) {
                if ((e.ctrlKey && e.key === 'r') || e.key === 'F5') {
                    e.preventDefault();
                    location.reload();
                }
            });
        """)

    window.events.loaded += on_loaded

    print("[OK] Window created, opening...")
    webview.start(debug=args.dev)


if __name__ == '__main__':
    main()
