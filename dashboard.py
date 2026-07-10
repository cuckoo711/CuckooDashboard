#!/usr/bin/env python3
"""
MiMo Usage Dashboard
Web 看板服务器，用于副屏显示 MiMo 使用情况。

使用方式:
    python dashboard.py              # 默认端口 5000
    python dashboard.py --port 8080  # 指定端口
    python dashboard.py --open       # 自动打开浏览器
"""

import argparse
import json
import os
import secrets
import sys
import time
from urllib.parse import urlparse

try:
    from flask import Flask, abort, jsonify, send_from_directory, request
except ImportError:
    print("错误: 缺少 Flask 库，请运行: pip install flask")
    sys.exit(1)

try:
    from flask_sock import Sock
except ImportError:
    print("错误: 缺少 flask-sock 库，请运行: pip install flask-sock")
    sys.exit(1)

from services.config import load_config
from services.github_service import get_github_data
from services.media_service import (
    get_media_info,
    load_lyric_offset,
    normalize_lyric_offset,
    reload_current_media,
    save_lyric_offset,
    set_current_song_id,
)
from services.mimo_service import fetch_all_data, get_mimo_api
from services.nug_service import get_nug_payload
from services.system_service import get_system_info
from services.theme import (
    THEMES,
    load_theme_index,
    next_theme_index,
    save_theme_index,
    theme_index_by_name,
    theme_response,
)

app = Flask(__name__, static_folder="static")
sock = Sock(app)

# ============================================================
# WebSocket 统一数据推送
# ============================================================

import threading as _ws_threading

_ws_clients = []
_ws_client_states = {}
_ws_clients_lock = _ws_threading.Lock()
_ws_vibe_coding = False  # 前端 Vibe Coding 状态（任一客户端开启即生效）
_ws_broadcaster_started = False
_ws_broadcaster_lock = _ws_threading.Lock()


def _ws_recalc_vibe_locked() -> bool:
    """在已持有 _ws_clients_lock 时重新计算全局 Vibe Coding 状态。"""
    global _ws_vibe_coding
    _ws_vibe_coding = any(s.get("vibe") for s in _ws_client_states.values())
    return _ws_vibe_coding


def _ws_has_clients() -> bool:
    """线程安全地判断是否有 WebSocket 客户端。"""
    with _ws_clients_lock:
        return bool(_ws_clients)


def _ws_clients_snapshot() -> list:
    """线程安全地获取 WebSocket 客户端快照。"""
    with _ws_clients_lock:
        return list(_ws_clients)


def _ws_broadcast(msg: dict):
    """向所有连接的客户端广播消息，失败时自动清理。"""
    data = json.dumps(msg, ensure_ascii=False)
    dead = []
    for ws in _ws_clients_snapshot():
        try:
            ws.send(data)
        except Exception:
            dead.append(ws)
    if dead:
        with _ws_clients_lock:
            for ws in dead:
                if ws in _ws_clients:
                    _ws_clients.remove(ws)
                _ws_client_states.pop(ws, None)
            _ws_recalc_vibe_locked()


@sock.route("/ws")
def ws_handler(ws):
    """WebSocket 端点：前端建立连接后接收后端推送 + 前端指令。"""
    with _ws_clients_lock:
        _ws_clients.append(ws)
        _ws_client_states[ws] = {"vibe": False}
        total = len(_ws_clients)
    print(f"[ws] client connected (total: {total})", flush=True)
    try:
        while ws.connected:
            raw = ws.receive(timeout=30)
            if raw:
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "vibe":
                        with _ws_clients_lock:
                            _ws_client_states.setdefault(ws, {})["vibe"] = bool(msg.get("active"))
                            vibe = _ws_recalc_vibe_locked()
                        print(f"[ws] vibe coding: {'ON' if vibe else 'OFF'}", flush=True)
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass
    finally:
        with _ws_clients_lock:
            if ws in _ws_clients:
                _ws_clients.remove(ws)
            _ws_client_states.pop(ws, None)
            vibe = _ws_recalc_vibe_locked()
            total = len(_ws_clients)
        print(f"[ws] client disconnected (total: {total}, vibe: {'ON' if vibe else 'OFF'})", flush=True)


def _ws_broadcaster():
    """后台线程：并行获取 system + media，定时广播。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _executor = ThreadPoolExecutor(max_workers=4)
    _nug_counter = 0
    while True:
        t0 = time.time()
        try:
            if _ws_has_clients():
                # system + media 并行获取
                futs = {
                    _executor.submit(get_system_info): "system",
                    _executor.submit(get_media_info): "media",
                }
                for fut in as_completed(futs):
                    msg_type = futs[fut]
                    try:
                        _ws_broadcast({"type": msg_type, "data": fut.result()})
                    except Exception as e:
                        print(f"[ws] {msg_type} broadcast error: {e}", flush=True)

                # mimo + nug：Coding 模式 20 秒，Chilling 模式 60 秒
                _nug_counter += 1
                mimo_interval = 20 if _ws_vibe_coding else 60
                if _nug_counter >= mimo_interval:
                    _nug_counter = 0
                    try:
                        _ws_broadcast({"type": "mimo", "data": fetch_all_data()})
                    except Exception as e:
                        print(f"[ws] mimo broadcast error: {e}", flush=True)
                    try:
                        _ws_broadcast({"type": "nug", "data": get_nug_payload()})
                    except Exception as e:
                        print(f"[ws] nug broadcast error: {e}", flush=True)
        except Exception as e:
            print(f"[ws] broadcaster error: {e}", flush=True)
        # 精确计时：扣除执行耗时，保证 1 秒间隔
        elapsed = time.time() - t0
        time.sleep(max(0, 1.0 - elapsed))


def start_background_threads_once() -> bool:
    """启动后台线程；多次调用只会真正启动一次。"""
    global _ws_broadcaster_started
    with _ws_broadcaster_lock:
        if _ws_broadcaster_started:
            return False
        t = _ws_threading.Thread(target=_ws_broadcaster, daemon=True, name="ws-broadcaster")
        t.start()
        _ws_broadcaster_started = True
        return True

_DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN") or secrets.token_urlsafe(24)


def get_dashboard_token() -> str:
    """获取 POST 防护 token；优先读取配置/环境变量，否则使用启动时随机值。"""
    config = load_config()
    token = (config.get("dashboard") or {}).get("token") or os.environ.get("DASHBOARD_TOKEN")
    return str(token or _DASHBOARD_TOKEN)


def _same_site_from_header(value: str | None) -> bool:
    """校验 Origin/Referer 是否指向当前 dashboard 同源。"""
    if not value:
        return False
    try:
        parsed = urlparse(value)
        host_url = urlparse(request.host_url)
        return parsed.scheme == host_url.scheme and parsed.netloc == host_url.netloc
    except Exception:
        return False


def require_post_protection():
    """对本地状态修改类 POST 做最小 CSRF/token 防护。"""
    if request.method != "POST":
        return
    expected = get_dashboard_token()
    provided = request.headers.get("X-Dashboard-Token")
    if provided and secrets.compare_digest(provided, expected):
        return
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if _same_site_from_header(origin) or _same_site_from_header(referer):
        return
    abort(403)


# ============================================================
# 显示主题管理
# ============================================================


def _set_theme_response(idx: int) -> dict:
    """保存主题并广播给所有客户端。"""
    save_theme_index(idx)
    data = theme_response(idx)
    _ws_broadcast({"type": "theme", "data": data})
    return data


@app.route("/api/theme", methods=["GET", "POST"])
def api_theme_get_or_set():
    """GET 返回当前主题；POST 指定主题。"""
    if request.method == "GET":
        return jsonify(theme_response(load_theme_index()))
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    idx = theme_index_by_name(payload.get("theme"))
    if idx is None:
        return jsonify({"error": "unknown theme", "themes": [t["name"] for t in THEMES]}), 400
    return jsonify(_set_theme_response(idx))


@app.route("/api/theme/next", methods=["POST"])
def api_theme_next():
    """循环切换到下一个主题。"""
    require_post_protection()
    idx = next_theme_index()
    return jsonify(_set_theme_response(idx))


@app.after_request
def no_cache_static(response):
    """禁止浏览器缓存静态文件，改了 HTML/CSS/JS 刷新即生效，不用重启"""
    if request.path.startswith("/static/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/")
def index():
    """返回看板页面"""
    return send_from_directory("static", "dashboard.html")



@app.route("/api/data")
def api_data():
    """返回所有 MiMo 数据 + GitHub 贡献"""
    data = fetch_all_data()
    data["github"] = get_github_data()
    return jsonify(data)


# ============================================================
# SMTC 媒体信息 + 网易云歌词
# ============================================================


@app.route("/api/media")
def api_media():
    """返回当前播放的媒体信息和歌词"""
    return jsonify(get_media_info())


@app.route("/api/media/reload", methods=["POST"])
def api_media_reload():
    """清除当前歌曲的歌词缓存并重新获取。"""
    require_post_protection()
    return jsonify(reload_current_media())


@app.route("/api/media/set_song_id", methods=["POST"])
def api_media_set_song_id():
    """手动指定当前歌曲的网易云 song_id 并加载对应歌词。"""
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    result, status = set_current_song_id(payload.get("song_id"))
    return jsonify(result), status


@app.route("/api/media/offset", methods=["GET", "POST"])
def api_media_offset():
    """GET: 返回当前偏移量; POST: 设置偏移量（支持 delta 增量或绝对值）"""
    if request.method == "GET":
        return jsonify({"offset": load_lyric_offset()})
    require_post_protection()
    val = normalize_lyric_offset(request.get_json(silent=True) or {})
    save_lyric_offset(val)
    return jsonify({"offset": val})


# ============================================================
# SMTC 系统级播放控制
# ============================================================


def _smtc_control(action: str) -> dict:
    """通过 Windows SMTC 发送系统级媒体控制指令"""
    import asyncio

    async def _do():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )
        manager = await MediaManager.request_async()
        session = manager.get_current_session()
        if not session:
            return {"ok": False, "error": "no active session"}

        props = await session.try_get_media_properties_async()
        title = props.title or ""

        if action == "next":
            ok = await session.try_skip_next_async()
        elif action == "prev":
            ok = await session.try_skip_previous_async()
        elif action == "play":
            ok = await session.try_play_async()
        elif action == "pause":
            ok = await session.try_pause_async()
        elif action == "toggle":
            ok = await session.try_toggle_play_pause_async()
        else:
            return {"ok": False, "error": "unknown action"}

        return {"ok": ok, "title": title}

    try:
        return asyncio.run(_do())
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/player/<action>", methods=["POST"])
def api_player_control(action):
    """播放控制：play/pause/next/prev/toggle（通过 Windows SMTC 系统级指令）"""
    require_post_protection()
    allowed = {"play", "pause", "next", "prev", "toggle"}
    if action not in allowed:
        return jsonify({"error": "unknown action"}), 400
    result = _smtc_control(action)
    return jsonify(result), 200 if result.get("ok") else 500


@app.route("/api/system")
def api_system():
    """返回系统硬件信息（独立端点，不依赖 MiMo 登录）"""
    return jsonify(get_system_info())


@app.route("/api/nug")
def api_nug():
    """返回 NUG 平台余额和用量"""
    return jsonify(get_nug_payload())


def main():
    parser = argparse.ArgumentParser(description="MiMo Usage Dashboard")
    parser.add_argument("--port", "-p", type=int, default=5000, help="端口号 (默认 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--open", "-o", action="store_true", help="自动打开浏览器")
    parser.add_argument("--dev", action="store_true", help="开发模式：启用 Flask debug/reloader")
    args = parser.parse_args()

    if args.open:
        import webbrowser
        url = f"http://{args.host}:{args.port}"
        print(f"正在打开浏览器: {url}")
        webbrowser.open(url)

    print("MiMo Dashboard 启动中...")
    print(f"访问地址: http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("[security] 当前不是仅本机监听；POST 接口会要求同源或 X-Dashboard-Token")
    print("按 Ctrl+C 停止服务器")

    # Flask reloader 会先启动父进程；只在实际服务进程中启动后台线程。
    if not args.dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_threads_once()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.dev,
        use_reloader=args.dev,
        extra_files=["static/dashboard.html"] if args.dev else None,
    )


if __name__ == "__main__":
    main()
