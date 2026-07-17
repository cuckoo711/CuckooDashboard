"""Dashboard page and public dashboard API routes."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from app.security import require_post_protection
from features.dashboard.service import get_dashboard_data
from features.devices.access import require_device_access
from runtime.lifecycle import get_runtime
from services.github_service import get_github_data
from services.health_service import get_health_snapshot
from services.off_peak_service import get_off_peak_badge_config

logger = logging.getLogger("cuckoo.dashboard")

blueprint = Blueprint("dashboard", __name__)


@blueprint.route("/")
def index():
    """返回看板页面。"""
    return send_from_directory(current_app.static_folder, "dashboard.html")


@blueprint.route("/api/data")
def api_data():
    """返回今日聚合数据、可配置 Vibe 卡片数据和 GitHub 贡献。"""
    ok, payload, status = require_device_access()
    if not ok:
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response, status
    data = get_dashboard_data()
    data["github"] = get_github_data()
    return jsonify(data)


@blueprint.route("/api/health")
def api_health():
    """返回轻量服务健康摘要，不主动刷新外部数据。"""
    return jsonify(get_health_snapshot())


@blueprint.route("/api/off-peak-badge")
def api_off_peak_badge():
    """返回顶部闲时倍率标签的配置。"""
    return jsonify(get_off_peak_badge_config())


@blueprint.route("/api/vibe", methods=["GET", "POST"])
def api_vibe():
    """Vibe Coding 状态：GET 读取持久化值，POST 设置并广播。"""
    websocket = get_runtime().websocket
    if request.method == "GET":
        return jsonify({"active": websocket.get_vibe()})

    require_post_protection()
    payload = request.get_json(silent=True) or {}
    active = websocket.set_vibe(bool(payload.get("active")))
    logger.info("[api] vibe coding: %s", "ON" if active else "OFF")
    return jsonify({"active": active})
