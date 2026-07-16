"""Workspace manifest API routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

blueprint = Blueprint("workspaces", __name__)


@blueprint.get("/api/workspaces/<workspace_id>")
def get_workspace_manifest(workspace_id: str):
    registry = current_app.extensions["workspace_registry"]
    try:
        manifest = registry.serialize_workspace(workspace_id)
    except KeyError:
        response = jsonify({"error": "workspace_not_found", "workspace_id": workspace_id})
        response.headers["Cache-Control"] = "no-store"
        return response, 404
    response = jsonify(manifest)
    response.headers["Cache-Control"] = "no-store"
    return response
