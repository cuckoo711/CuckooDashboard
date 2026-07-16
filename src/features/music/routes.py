"""Music-stage page, spectrum, offset, and calibration routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from app.security import require_loopback_access, require_post_protection
from runtime.lifecycle import get_runtime
from services.spectrum_service import (
    acquire_spectrum,
    apply_calibration_suggestion,
    cancel_beat_calibration,
    get_calibration_status,
    get_spectrum_frame,
    get_spectrum_status,
    list_capture_devices,
    load_music_offsets,
    record_calibration_tap,
    release_spectrum,
    request_capture_restart,
    save_music_offsets,
    start_beat_calibration,
)

blueprint = Blueprint("music", __name__)


def _broadcast_music_offset(offsets: dict) -> None:
    get_runtime().websocket.broadcast({"type": "music_offset", "data": offsets})


@blueprint.route("/music")
def music_stage():
    """全屏音乐舞台：歌词 + 可选 loopback 频谱。"""
    return send_from_directory(current_app.static_folder, "music.html")


@blueprint.route("/api/music/offset", methods=["GET", "POST"])
def api_music_offset():
    """音乐舞台频谱/鼓点偏移：GET 读取，POST 设置。"""
    if request.method == "GET":
        return jsonify(load_music_offsets())
    require_post_protection()
    offsets = save_music_offsets(request.get_json(silent=True) or {})
    _broadcast_music_offset(offsets)
    return jsonify(offsets)


@blueprint.route("/api/music/capture-devices")
def api_music_capture_devices():
    """返回可选频谱采集设备（Loopback 优先）。"""
    require_loopback_access()
    advanced = str(request.args.get("advanced") or "").lower() in {"1", "true", "yes"}
    devices = list_capture_devices(include_advanced=advanced)
    status = get_spectrum_status()
    payload = {
        "devices": devices,
        "current": load_music_offsets().get("capture_device") or "auto",
        "status": {
            "available": status.get("available"),
            "device": status.get("device"),
            "error": status.get("error"),
            "has_audio_stack": status.get("has_audio_stack"),
            "has_soundcard": bool(status.get("has_audio_stack")),
        },
        "loopback_count": sum(1 for device in devices if device.get("kind") == "loopback"),
    }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.route("/api/music/capture-devices/refresh", methods=["POST"])
def api_music_capture_devices_refresh():
    """强制重枚举并可选重开采集。"""
    require_loopback_access()
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    if "capture_device" in payload:
        save_music_offsets({"capture_device": payload.get("capture_device")})
    else:
        request_capture_restart("manual refresh")
    devices = list_capture_devices(include_advanced=bool(payload.get("advanced")))
    return jsonify(
        {
            "ok": True,
            "devices": devices,
            "current": load_music_offsets().get("capture_device") or "auto",
            "loopback_count": sum(
                1 for device in devices if device.get("kind") == "loopback"
            ),
            "status": get_spectrum_status(),
        }
    )


@blueprint.route("/api/music/spectrum")
def api_music_spectrum():
    """最新频谱帧（REST 兜底）。"""
    return jsonify(get_spectrum_frame())


@blueprint.route("/api/music/spectrum/status")
def api_music_spectrum_status():
    """频谱采集栈与订阅状态。"""
    return jsonify(get_spectrum_status())


@blueprint.route("/api/music/spectrum/acquire", methods=["POST"])
def api_music_spectrum_acquire():
    """手动增加频谱兴趣计数。"""
    require_post_protection()
    acquire_spectrum()
    return jsonify(get_spectrum_status())


@blueprint.route("/api/music/spectrum/release", methods=["POST"])
def api_music_spectrum_release():
    """无 token 地安全减少频谱兴趣计数，供 sendBeacon 使用。"""
    release_spectrum()
    return jsonify({"ok": True})


@blueprint.route("/api/music/calibrate", methods=["GET", "POST"])
def api_music_calibrate():
    """读取或执行鼓点一键校准。"""
    if request.method == "GET":
        return jsonify(get_calibration_status())

    require_post_protection()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "start").lower()
    if action == "start":
        result = start_beat_calibration(float(payload.get("duration_s") or 6))
        _broadcast_music_offset(load_music_offsets())
        return jsonify(result)
    if action == "tap":
        result = record_calibration_tap(payload.get("client_ts"))
        if result.get("applied"):
            _broadcast_music_offset(load_music_offsets())
        return jsonify(result)
    if action == "apply":
        result = apply_calibration_suggestion()
        if result.get("ok"):
            _broadcast_music_offset(load_music_offsets())
        return jsonify(result)
    if action == "cancel":
        return jsonify(cancel_beat_calibration())
    return jsonify({"ok": False, "error": "unknown action"}), 400
