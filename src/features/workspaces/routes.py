"""Public workspace manifest and page routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, send_from_directory

from workspaces.repository import WorkspaceNotFoundError

blueprint = Blueprint("workspaces", __name__)


def _service():
    return current_app.extensions["workspace_service"]


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.get("/api/workspaces")
def list_workspace_manifests():
    return _no_store(jsonify({"workspaces": _service().list_workspaces()}))


@blueprint.get("/api/workspaces/<workspace_id>")
def get_workspace_manifest(workspace_id: str):
    try:
        manifest = _service().serialize(workspace_id)
    except WorkspaceNotFoundError:
        response = jsonify({"error": "workspace_not_found", "workspace_id": workspace_id})
        return _no_store(response), 404
    return _no_store(jsonify(manifest))


@blueprint.get("/workspaces/<workspace_id>")
def workspace_page(workspace_id: str):
    try:
        _service().get(workspace_id)
    except WorkspaceNotFoundError:
        return jsonify({"error": "workspace_not_found", "workspace_id": workspace_id}), 404
    return send_from_directory(current_app.static_folder, "dashboard.html")
