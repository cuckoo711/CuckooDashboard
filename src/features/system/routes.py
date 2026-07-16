"""System-monitoring API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify

from services.system_service import get_system_info

blueprint = Blueprint("system", __name__)


@blueprint.route("/api/system")
def api_system():
    """返回系统硬件信息（独立端点，不依赖任何 Provider 认证）。"""
    return jsonify(get_system_info())
