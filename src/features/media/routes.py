"""Media and player HTTP routes."""

from __future__ import annotations

import logging

from flask import Blueprint, Response, abort, jsonify, request

from app.security import require_post_protection
from runtime.lifecycle import get_runtime
from services.media_service import (
    get_cover_ambient_bytes,
    get_cover_bytes,
    get_media_info,
    load_lyric_offset,
    normalize_lyric_offset,
    reload_current_media,
    save_lyric_offset,
)
from services.player_service import ALLOWED_PLAYER_ACTIONS, control_player

logger = logging.getLogger("cuckoo.features.media")

blueprint = Blueprint("media", __name__)


@blueprint.route("/api/media")
def api_media():
    """返回当前播放的媒体信息和歌词。"""
    return jsonify(get_media_info())


@blueprint.route("/api/media/cover")
def api_media_cover():
    """返回当前曲目封面图（SMTC thumbnail）。无封面时 404。"""
    data, mime = get_cover_bytes()
    if not data:
        abort(404)
    response = Response(data, mimetype=mime or "image/jpeg")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@blueprint.route("/api/media/cover/ambient")
def api_media_cover_ambient():
    """返回后端预模糊/降亮的封面氛围图。无封面时 404。"""
    data, mime = get_cover_ambient_bytes()
    if not data:
        abort(404)
    response = Response(data, mimetype=mime or "image/jpeg")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@blueprint.route("/api/media/reload", methods=["POST"])
def api_media_reload():
    """清除当前歌曲的歌词缓存并重新获取。"""
    require_post_protection()
    return jsonify(reload_current_media())


@blueprint.route("/api/media/offset", methods=["GET", "POST"])
def api_media_offset():
    """GET 返回当前偏移量；POST 设置增量或绝对偏移量。"""
    if request.method == "GET":
        return jsonify({"offset": load_lyric_offset()})

    require_post_protection()
    value = normalize_lyric_offset(request.get_json(silent=True) or {})
    save_lyric_offset(value)
    try:
        hub = get_runtime().websocket
        hub.broadcast_media(get_media_info())
        hub.force_lyric_sync()
    except Exception as exc:
        logger.debug("[media] offset broadcast failed: %s", exc)
    return jsonify({"offset": value})


@blueprint.route("/api/player/<action>", methods=["POST"])
def api_player_control(action):
    """通过 Windows SMTC 执行系统级播放控制。"""
    require_post_protection()
    if action not in ALLOWED_PLAYER_ACTIONS:
        return jsonify({"error": "unknown action"}), 400
    result = control_player(action)
    if result.get("ok"):
        return jsonify(result)
    # “当前没有活动媒体会话”是正常运行状态而不是服务器故障，不应报 500。
    status = 409 if result.get("error") == "no active session" else 500
    return jsonify(result), status
