"""Theme and font API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.security import require_loopback_access, require_post_protection
from features.appearance.service import get_font_payload
from runtime.lifecycle import get_runtime
from services.font_service import (
    FontError,
    delete_font,
    list_fonts,
    upload_font,
)
from services.theme import (
    THEMES,
    load_theme_index,
    next_theme_index,
    save_theme_index,
    theme_index_by_name,
    theme_response,
)

blueprint = Blueprint("appearance", __name__)


def _set_theme_response(idx: int) -> dict:
    """保存主题并广播给所有客户端。"""
    save_theme_index(idx)
    data = theme_response(idx)
    get_runtime().hub.broadcast({"type": "theme", "data": data})
    return data


@blueprint.route("/api/theme", methods=["GET", "POST"])
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


@blueprint.route("/api/theme/next", methods=["POST"])
def api_theme_next():
    """循环切换到下一个主题。"""
    require_post_protection()
    idx = next_theme_index()
    return jsonify(_set_theme_response(idx))


@blueprint.route("/api/font")
def api_font():
    """返回当前生效字体（enabled + filename + url）。看板页据此动态注入 @font-face。"""
    return jsonify(get_font_payload())


@blueprint.route("/api/fonts")
def api_fonts_list():
    """列出 fonts/ 目录下所有字体。settings 页面下拉使用。"""
    require_loopback_access()
    return jsonify({"fonts": list_fonts()})


@blueprint.route("/api/fonts/upload", methods=["POST"])
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


@blueprint.route("/api/fonts/delete", methods=["POST"])
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
    get_runtime().hub.broadcast({"type": "font", "data": get_font_payload()})
    return jsonify(result)
