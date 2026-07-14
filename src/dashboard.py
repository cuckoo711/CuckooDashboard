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
import ipaddress
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from core.config import load_config
from core.logging_config import setup_logging

# 初始化日志系统（需在其他模块 import 之前完成）
setup_logging(load_config())

logger = logging.getLogger("cuckoo.dashboard")

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

from services.font_service import (
    FontError,
    delete_font,
    list_fonts,
    upload_font,
)
from services.github_service import get_github_data
from services.health_service import get_health_snapshot
from services.off_peak_service import get_off_peak_badge_config
from services.media_service import (
    get_media_info,
    load_lyric_offset,
    normalize_lyric_offset,
    reload_current_media,
    save_lyric_offset,
)
from providers import fetch_all_data, get_nug_payload, get_nug_channel_breakdown
from services.player_service import ALLOWED_PLAYER_ACTIONS, control_player
from services.settings_service import (
    SettingsValidationError,
    get_settings_payload,
    reveal_secret,
    save_settings_payload,
)
from services.system_service import get_system_info
from services.vibe_data_service import get_vibe_data
from services.theme import (
    THEMES,
    load_theme_index,
    next_theme_index,
    save_theme_index,
    theme_index_by_name,
    theme_response,
)

app = Flask(__name__, static_folder="static")
# 字体上传走 JSON body（base64），20MB 原始文件约 27MB base64，留一些余量。
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
sock = Sock(app)


def _get_font_payload() -> dict:
    """从配置读取当前字体和字号设置并生成前端 payload。

    ``enabled`` 且指定的文件确实存在于 fonts/ 才返回 url；否则回落到系统字体。
    """
    from services.font_service import font_exists as _font_exists
    dashboard_cfg = (load_config().get("dashboard") or {})
    font_cfg = dashboard_cfg.get("font") or {}
    enabled = bool(font_cfg.get("enabled"))
    filename = str(font_cfg.get("filename") or "")
    active = enabled and filename and _font_exists(filename)
    font_size_cfg = dashboard_cfg.get("font_size") or {}
    return {
        "enabled": enabled,
        "filename": filename,
        "url": f"/static/fonts/{filename}" if active else "",
        "active": bool(active),
        "font_size": {
            "title_text": str(font_size_cfg.get("title_text") or "Cuckoo Dashboard"),
            "title":     int(font_size_cfg.get("title", 16)),
            "clock":     int(font_size_cfg.get("clock", 22)),
            "date":      int(font_size_cfg.get("date", 15)),
            "card_head": int(font_size_cfg.get("card_head", 10)),
            "card_foot": int(font_size_cfg.get("card_foot", 10)),
            "card_body": int(font_size_cfg.get("card_body", 10)),
            "offset":    int(font_size_cfg.get("offset", 0)),
        },
    }


def _get_dashboard_data() -> dict:
    """组合既有今日用量与可配置的 Vibe 卡片数据。

    ``fetch_all_data`` 可携带由任意聚合器产生的私有 Provider 快照；这里将其
    传给 Vibe 服务复用后移除，避免向 API/WS 客户端泄露内部缓存结构。
    """
    data = dict(fetch_all_data())
    snapshots = data.pop("_provider_snapshots", {})
    data["vibe"] = get_vibe_data(prefetched_provider_data=snapshots)
    return data


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

# ── Vibe Coding 状态持久化 ──


def _load_vibe_state() -> bool:
    """从配置加载 Vibe Coding 状态。"""
    from core.config import load_config as _lc
    return bool(_lc().get("vibe_active", False))


def _save_vibe_state(active: bool):
    """将 Vibe Coding 状态持久化到配置。"""
    from core.config import set_config_value
    set_config_value("vibe_active", active)


# 启动时恢复上次的 Vibe 状态
_ws_vibe_coding = _load_vibe_state()


def _ws_send_vibe(ws):
    """立即发送保存的 vibe 状态给单个客户端。"""
    try:
        ws.send(json.dumps({"type": "vibe_state", "data": {"active": _load_vibe_state()}}, ensure_ascii=False))
    except Exception:
        pass


def _ws_send_all_data(ws):
    """向单个客户端立即推送全部数据（连接时一次性调用）。
    Vibe 状态最先发，随后发送包含可配置环图、模型条和余额的聚合数据；
    每一步都独立 try/except，避免任何 Provider 异常打断整个推送链路。
    """
    def _send(msg_type, data):
        try:
            ws.send(json.dumps({"type": msg_type, "data": data}, ensure_ascii=False))
            logger.info(f"[ws] init: sent {msg_type}")
        except Exception as e:
            logger.error(f"[ws] init {msg_type} error: {e}")

    def _safe_send(msg_type, getter):
        try:
            _send(msg_type, getter())
        except Exception as e:
            logger.error(f"[ws] init {msg_type} fetch error: {e}")

    # 最先发 vibe 状态：保证 UI 立即能显示正确的 Coding/Chilling 标签，
    # 即便后续任何 provider 抛异常也不会影响。
    _ws_send_vibe(ws)
    _safe_send("dashboard_data", _get_dashboard_data)
    _safe_send("github", get_github_data)
    _safe_send("media", get_media_info)
    # system 最慢，放最后
    _safe_send("system", get_system_info)
    try:
        from services.theme import load_theme_index, theme_response
        _send("theme", theme_response(load_theme_index()))
    except Exception:
        pass
    _safe_send("font", _get_font_payload)
    logger.info("[ws] init: all data sent")


def _ws_recalc_vibe_locked() -> bool:
    """在已持有 _ws_clients_lock 时重新计算全局 Vibe Coding 状态。
    无客户端时保持配置中持久化的值。"""
    global _ws_vibe_coding
    if _ws_client_states:
        _ws_vibe_coding = any(s.get("vibe") for s in _ws_client_states.values())
    else:
        # 没有活跃客户端时，保持配置中的持久化值
        _ws_vibe_coding = _load_vibe_state()
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
    saved_vibe = _load_vibe_state()
    with _ws_clients_lock:
        _ws_clients.append(ws)
        _ws_client_states[ws] = {"vibe": saved_vibe}
        _ws_recalc_vibe_locked()
        total = len(_ws_clients)
    logger.info(f"[ws] client connected (total: {total})")
    # 异步推送全部数据（不阻塞 handler，避免多客户端排队等 system info）
    _ws_threading.Thread(target=_ws_send_all_data, args=(ws,), daemon=True).start()
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
                        _save_vibe_state(vibe)
                        logger.info(f"[ws] vibe coding: {'ON' if vibe else 'OFF'}")
                    elif msg.get("type") == "init":
                        _ws_send_all_data(ws)
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass
    finally:
        with _ws_clients_lock:
            if ws in _ws_clients:
                _ws_clients.remove(ws)
            _ws_client_states.pop(ws, None)
            total = len(_ws_clients)
        logger.info(f"[ws] client disconnected (total: {total})")


def _ws_broadcaster():
    """后台线程：并行获取 system + media，定时广播。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _executor = ThreadPoolExecutor(max_workers=4)
    _nug_counter = 0
    while True:
        t0 = time.time()
        try:
            if _ws_has_clients():
                # system + media + github 并行获取（每次都推）
                futs = {
                    _executor.submit(get_system_info): "system",
                    _executor.submit(get_media_info): "media",
                    _executor.submit(get_github_data): "github",
                }
                for fut in as_completed(futs):
                    msg_type = futs[fut]
                    try:
                        _ws_broadcast({"type": msg_type, "data": fut.result()})
                    except Exception as e:
                        logger.error(f"[ws] {msg_type} broadcast error: {e}")

                # Vibe 卡片：Coding 模式 20 秒，Chilling 模式 60 秒。
                # 环图、模型条和余额在同一个 payload 内刷新，避免跨消息拼装。
                _vibe_counter += 1
                vibe_interval = 20 if _ws_vibe_coding else 60
                if _vibe_counter >= vibe_interval:
                    _vibe_counter = 0
                    try:
                        _ws_broadcast({"type": "dashboard_data", "data": _get_dashboard_data()})
                    except Exception as e:
                        logger.error(f"[ws] vibe data broadcast error: {e}")

        except Exception as e:
            logger.error(f"[ws] broadcaster error: {e}")
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


def require_loopback_access():
    """限制配置后台只接受 127.0.0.1 / ::1 回环请求。"""
    remote_addr = request.remote_addr
    try:
        address = ipaddress.ip_address(remote_addr) if remote_addr else None
        is_loopback = bool(address and address.is_loopback)
        if not is_loopback and isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            is_loopback = address.ipv4_mapped.is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        abort(403, description="settings is only available from loopback")


@app.before_request
def protect_settings_static_assets():
    """阻止通过 Flask 默认 /static 路径绕过配置页的回环限制。"""
    if request.path.startswith("/static/settings"):
        require_loopback_access()


def _broadcast_settings_update():
    """广播配置更新后的非敏感运行时状态。"""
    _ws_broadcast({"type": "config_updated", "data": {"ok": True}})
    try:
        _ws_broadcast({"type": "theme", "data": theme_response(load_theme_index())})
    except Exception:
        pass
    try:
        _ws_broadcast({"type": "font", "data": _get_font_payload()})
    except Exception:
        pass
    try:
        active = _load_vibe_state()
        global _ws_vibe_coding
        with _ws_clients_lock:
            _ws_vibe_coding = active
            for state in _ws_client_states.values():
                state["vibe"] = active
        _ws_broadcast({"type": "vibe_state", "data": {"active": active}})
    except Exception:
        pass


# ============================================================
# 本地配置后台
# ============================================================


@app.route("/settings")
def settings_index():
    """配置后台页面；即使服务监听所有网卡也只允许回环访问。"""
    require_loopback_access()
    return send_from_directory("static", "settings.html")


@app.route("/settings-assets/<path:filename>")
def settings_assets(filename):
    """配置后台专用静态文件。"""
    require_loopback_access()
    if filename not in {"settings.css", "settings.js"}:
        abort(404)
    return send_from_directory("static", filename)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """读取或保存脱敏后的用户配置。"""
    require_loopback_access()
    if request.method == "GET":
        response = jsonify(get_settings_payload())
        response.headers["Cache-Control"] = "no-store"
        return response

    require_post_protection()
    try:
        result = save_settings_payload(request.get_json(silent=True) or {})
    except SettingsValidationError as exc:
        return jsonify({"error": exc.as_dict()}), 400
    except Exception:
        logger.exception("[settings] 保存配置失败")
        return jsonify({"error": {"message": "保存配置失败，请查看日志"}}), 500
    _broadcast_settings_update()
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/settings/reveal", methods=["POST"])
def api_settings_reveal():
    """按用户明确操作读取一个敏感字段。"""
    require_loopback_access()
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    try:
        path = payload.get("path")
        value = reveal_secret(
            path,
            identity=payload.get("identity"),
            field=payload.get("field"),
        )
        return jsonify({"path": path, "value": value})
    except SettingsValidationError as exc:
        return jsonify({"error": exc.as_dict()}), 400


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
    if (
        request.path.startswith("/static/")
        or request.path.startswith("/settings")
        or request.path.startswith("/api/settings")
        or request.path == "/"
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/")
def index():
    """返回看板页面"""
    return send_from_directory("static", "dashboard.html")



@app.route("/api/data")
def api_data():
    """返回今日聚合数据、可配置 Vibe 卡片数据和 GitHub 贡献。"""
    data = _get_dashboard_data()
    data["github"] = get_github_data()
    return jsonify(data)


@app.route("/api/health")
def api_health():
    """返回轻量服务健康摘要，不主动刷新外部数据。"""
    return jsonify(get_health_snapshot())


@app.route("/api/off-peak-badge")
def api_off_peak_badge():
    """返回顶部闲时倍率标签的配置。"""
    return jsonify(get_off_peak_badge_config())


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


@app.route("/api/media/offset", methods=["GET", "POST"])
def api_media_offset():
    """GET: 返回当前偏移量; POST: 设置偏移量（支持 delta 增量或绝对值）"""
    if request.method == "GET":
        return jsonify({"offset": load_lyric_offset()})
    require_post_protection()
    val = normalize_lyric_offset(request.get_json(silent=True) or {})
    save_lyric_offset(val)
    return jsonify({"offset": val})


@app.route("/api/vibe", methods=["GET", "POST"])
def api_vibe():
    """Vibe Coding 状态：GET 读取持久化值，POST 设置并广播。

    作为 WebSocket 通道的兜底：WS 尚未就绪或断连时，前端可通过 REST 读写状态。
    """
    if request.method == "GET":
        return jsonify({"active": _load_vibe_state()})
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    active = bool(payload.get("active"))
    _save_vibe_state(active)
    with _ws_clients_lock:
        global _ws_vibe_coding
        _ws_vibe_coding = active
        # 同步刷新所有已知客户端的 vibe 标记，避免下次 recalc 用旧值覆盖
        for state in _ws_client_states.values():
            state["vibe"] = active
    # 广播给其他窗口/客户端，确保多端同步
    _ws_broadcast({"type": "vibe_state", "data": {"active": active}})
    logger.info(f"[api] vibe coding: {'ON' if active else 'OFF'}")
    return jsonify({"active": active})


# ============================================================
# SMTC 系统级播放控制
# ============================================================


@app.route("/api/player/<action>", methods=["POST"])
def api_player_control(action):
    """播放控制：play/pause/next/prev/toggle（通过 Windows SMTC 系统级指令）"""
    require_post_protection()
    if action not in ALLOWED_PLAYER_ACTIONS:
        return jsonify({"error": "unknown action"}), 400
    result = control_player(action)
    return jsonify(result), 200 if result.get("ok") else 500


@app.route("/api/system")
def api_system():
    """返回系统硬件信息（独立端点，不依赖 MiMo 登录）"""
    return jsonify(get_system_info())


@app.route("/api/nug")
def api_nug():
    """返回 NUG 平台余额和用量"""
    return jsonify(get_nug_payload())


@app.route("/api/nug/channels")
def api_nug_channels():
    """返回 NUG 按 channel 分组的用量明细"""
    return jsonify(get_nug_channel_breakdown(days=7))


# ============================================================
# 字体管理
# ============================================================


@app.route("/api/font")
def api_font():
    """返回当前生效字体（enabled + filename + url）。看板页据此动态注入 @font-face。"""
    return jsonify(_get_font_payload())


@app.route("/api/fonts")
def api_fonts_list():
    """列出 fonts/ 目录下所有字体。settings 页面下拉使用。"""
    require_loopback_access()
    return jsonify({"fonts": list_fonts()})


@app.route("/api/fonts/upload", methods=["POST"])
def api_fonts_upload():
    """上传新的字体文件到 fonts/ 目录。仅本机回环可用。"""
    require_loopback_access()
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    try:
        result = upload_font(payload.get("filename", ""), payload.get("data", ""))
    except FontError as exc:
        return jsonify({"error": exc.as_dict()}), 400
    return jsonify(result)


@app.route("/api/fonts/delete", methods=["POST"])
def api_fonts_delete():
    """删除 fonts/ 目录下的字体文件。仅本机回环可用。"""
    require_loopback_access()
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    try:
        result = delete_font(payload.get("filename", ""))
    except FontError as exc:
        return jsonify({"error": exc.as_dict()}), 400
    # 如果删掉的是当前 enabled 的字体，前端拿到的 /api/font 会自动回落。
    _ws_broadcast({"type": "font", "data": _get_font_payload()})
    return jsonify(result)


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
        logger.info(f"正在打开浏览器: {url}")
        webbrowser.open(url)

    logger.info("MiMo Dashboard 启动中...")
    logger.info(f"访问地址: http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.info("[security] 当前不是仅本机监听；POST 接口会要求同源或 X-Dashboard-Token")
    logger.info("按 Ctrl+C 停止服务器")

    # Flask reloader 会先启动父进程；只在实际服务进程中启动后台线程。
    if not args.dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_threads_once()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.dev,
        use_reloader=args.dev,
        extra_files=[
            str(Path(__file__).parent / "static" / "dashboard.html"),
            str(Path(__file__).parent / "static" / "dashboard.css"),
            str(Path(__file__).parent / "static" / "dashboard.js"),
            str(Path(__file__).parent / "static" / "settings.html"),
            str(Path(__file__).parent / "static" / "settings.css"),
            str(Path(__file__).parent / "static" / "settings.js"),
        ] if args.dev else None,
    )


if __name__ == "__main__":
    main()
