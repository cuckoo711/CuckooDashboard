#!/usr/bin/env python3
"""
MiMo Usage Desktop App
原生桌面应用，无需打开浏览器。

使用方式:
    python desktop.py              # 默认端口 5000
    python desktop.py --port 8080  # 指定端口
    python desktop.py --dev        # 开发模式（显示控制台）
"""

import argparse
import ctypes
import logging
import socket
import sys
import threading
import time

import webview

# 导入Flask应用
from dashboard import app, get_mimo_api, start_background_threads_once


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
    app.run(host='127.0.0.1', port=port, debug=debug, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(description='MiMo Usage Desktop App')
    parser.add_argument('--port', type=int, default=5000, help='服务器端口 (默认: 5000)')
    parser.add_argument('--dev', action='store_true', help='开发模式 (显示Flask日志)')
    args = parser.parse_args()

    # Windows DPI 设置
    enable_dpi_awareness()

    # 非开发模式下静默Flask日志
    if not args.dev:
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

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
        width=960,
        height=640,
        resizable=True,
        frameless=True,
        text_select=True,
        on_top=True,
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
