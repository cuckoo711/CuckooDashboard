"""Public workspace manifest and page routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, send_from_directory

from features.devices.access import require_device_access
from workspaces.repository import WorkspaceNotFoundError

blueprint = Blueprint("workspaces", __name__)


def _service():
    return current_app.extensions["workspace_service"]


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.get("/api/workspaces")
def list_workspace_manifests():
    ok, payload, status = require_device_access()
    if not ok:
        return _no_store(jsonify(payload)), status
    device = payload["device"]
    assigned = str(device.get("workspace_id") or "main")
    try:
        workspace = _service().serialize(assigned)
    except WorkspaceNotFoundError:
        return _no_store(jsonify({
            "error": "workspace_not_found",
            "workspace_id": assigned,
        })), 404
    return _no_store(jsonify({"workspaces": [workspace]}))


@blueprint.get("/api/workspaces/<workspace_id>")
def get_workspace_manifest(workspace_id: str):
    ok, payload, status = require_device_access(workspace_id)
    if not ok:
        return _no_store(jsonify(payload)), status
    try:
        manifest = _service().serialize(workspace_id)
    except WorkspaceNotFoundError:
        response = jsonify({"error": "workspace_not_found", "workspace_id": workspace_id})
        return _no_store(response), 404
    return _no_store(jsonify(manifest))


@blueprint.get("/workspaces/<workspace_id>")
def workspace_page(workspace_id: str):
    # HTML shell stays public so unapproved terminals can show the pending gate.
    # Manifests and aggregate data remain protected by device approval.
    try:
        _service().get(workspace_id)
    except WorkspaceNotFoundError:
        return jsonify({"error": "workspace_not_found", "workspace_id": workspace_id}), 404
    return send_from_directory(current_app.static_folder, "dashboard.html")
