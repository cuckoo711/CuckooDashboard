"""Settings page and local configuration API routes."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory

from app.security import require_loopback_access, require_post_protection
from runtime.lifecycle import get_runtime
from features.settings.persistence import reveal_secret
from features.settings.schema import SettingsValidationError
from features.settings.service import get_settings_payload, save_settings_payload
from workspaces.repository import WorkspaceNotFoundError

logger = logging.getLogger("cuckoo.dashboard")

blueprint = Blueprint("settings", __name__)


@blueprint.route("/settings")
def settings_index():
    """配置后台页面；即使服务监听所有网卡也只允许回环访问。"""
    require_loopback_access()
    return send_from_directory(current_app.static_folder, "settings.html")


@blueprint.route("/settings-assets/<path:filename>")
def settings_assets(filename):
    """配置后台专用静态文件。"""
    require_loopback_access()
    if filename in {"settings.css", "settings.js"}:
        return send_from_directory(current_app.static_folder, filename)

    if not filename.startswith("modules/"):
        abort(404)
    module_filename = filename.removeprefix("modules/")
    path_parts = module_filename.split("/")
    if (
        not module_filename.endswith(".js")
        or "\\" in module_filename
        or any(part in {"", ".", ".."} for part in path_parts)
    ):
        abort(404)
    modules_directory = Path(current_app.static_folder) / "settings" / "modules"
    return send_from_directory(modules_directory, module_filename)


@blueprint.route("/api/settings", methods=["GET", "POST"])
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
    get_runtime().hub.broadcast_settings_update()
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.route("/api/settings/reload-clients", methods=["POST"])
def api_settings_reload_clients():
    """通过 WebSocket 让所有看板页面立即刷新。"""
    require_loopback_access()
    require_post_protection()
    get_runtime().hub.broadcast({"type": "reload"})
    return jsonify({"ok": True})


@blueprint.route("/api/settings/clients")
def api_settings_clients():
    """返回当前所有 WebSocket 客户端列表（仅回环）。"""
    require_loopback_access()
    return jsonify({"clients": get_runtime().hub.list_clients()})


@blueprint.route("/api/settings/clients/<client_id>/navigate", methods=["POST"])
def api_settings_navigate_client(client_id):
    """向指定客户端发送页面切换指令（仅回环）。"""
    require_loopback_access()
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    workspace_id = payload.get("workspace_id")
    target_page = payload.get("page")
    runtime = get_runtime()
    if workspace_id is not None:
        workspace_id = str(workspace_id).strip()
        if not workspace_id:
            return jsonify({"error": {"message": "workspace_id 不能为空"}}), 400
        try:
            current_app.extensions["workspace_service"].get_workspace(workspace_id)
        except WorkspaceNotFoundError:
            return jsonify({"error": {"message": "目标工作区不存在"}}), 404
        target_page = "dashboard"
    elif target_page not in ("dashboard", "music"):
        return jsonify({"error": {"message": "page 必须是 dashboard 或 music，或提供 workspace_id"}}), 400
    try:
        sent = (
            runtime.hub.navigate_client(client_id, workspace_id=workspace_id)
            if workspace_id is not None
            else runtime.hub.navigate_client(client_id, target_page)
        )
    except Exception:
        return jsonify({"error": {"message": "发送失败，客户端可能已断开"}}), 500
    if not sent:
        return jsonify({"error": {"message": "未找到该客户端"}}), 404
    return jsonify({"ok": True, "page": target_page, "workspace_id": workspace_id})


@blueprint.route("/api/settings/clients/<client_id>/screenshot", methods=["POST"])
def api_settings_screenshot_client(client_id):
    """向指定客户端发送截图指令（仅回环）。"""
    require_loopback_access()
    require_post_protection()
    try:
        request_id = get_runtime().hub.request_screenshot(client_id)
    except Exception:
        return jsonify({"error": {"message": "发送失败，客户端可能已断开"}}), 500
    if request_id is None:
        return jsonify({"error": {"message": "未找到该客户端"}}), 404
    return jsonify({"ok": True, "request_id": request_id})


@blueprint.route("/api/settings/reveal", methods=["POST"])
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
