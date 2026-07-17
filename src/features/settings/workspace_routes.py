"""Loopback-only workspace CRUD routes for Settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from features.settings.security import require_settings_access
from runtime.lifecycle import get_runtime
from workspaces.repository import (
    RequiredWorkspaceError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
)
from workspaces.service import WorkspaceInUseError, WorkspaceValidationError

blueprint = Blueprint("settings_workspaces", __name__)


def _service():
    return current_app.extensions["workspace_service"]


def _error(code: str, message: str, **details: Any):
    payload = {"code": code, "message": message, **details}
    return jsonify({"error": payload})


def _payload() -> Mapping[str, Any]:
    value = request.get_json(silent=True)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise WorkspaceValidationError("request body must be an object")
    return value


def _revision_from_request(payload: Mapping[str, Any] | None = None) -> int | None:
    value: Any = (payload or {}).get("revision")
    if value is None:
        value = request.args.get("revision")
    if value is None:
        value = request.headers.get("If-Match")
        if value:
            value = value.removeprefix("W/").strip('"')
    if value is None or value == "":
        return None
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceValidationError("revision must be a positive integer", "revision") from exc
    if revision < 1:
        raise WorkspaceValidationError("revision must be a positive integer", "revision")
    return revision


def client_size_summary() -> dict[str, Any]:
    """Read runtime client viewport summaries without coupling to websocket internals."""
    try:
        clients = get_runtime().hub.list_clients()
    except Exception:
        clients = []
    sizes = []
    for client in clients if isinstance(clients, list) else []:
        if not isinstance(client, Mapping):
            continue
        width = client.get("workspace_width") or client.get("viewport_width")
        height = client.get("workspace_height") or client.get("viewport_height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)):
            sizes.append({"width": width, "height": height})
    return {"client_count": len(sizes), "sizes": sizes}


def _broadcast(operation: str, workspace_id: str, revision: int | None) -> None:
    get_runtime().hub.broadcast(
        {
            "type": "workspace_updated",
            "data": {
                "workspace_id": workspace_id,
                "revision": revision,
                "operation": operation,
            },
        }
    )


def _handle_error(exc: Exception):
    if isinstance(exc, WorkspaceValidationError):
        return jsonify({"error": exc.as_dict()}), 400
    if isinstance(exc, WorkspaceNotFoundError):
        workspace_id = str(exc)
        return _error(
            "workspace_not_found",
            "workspace not found",
            workspace_id=workspace_id,
        ), 404
    if isinstance(exc, WorkspaceInUseError):
        return _error("workspace_in_use", str(exc)), 409
    if isinstance(exc, RequiredWorkspaceError):
        return _error("required_workspace", str(exc)), 409
    if isinstance(exc, WorkspaceConflictError):
        details = {}
        if exc.current_revision is not None:
            details["current_revision"] = exc.current_revision
        return _error("workspace_conflict", str(exc), **details), 409
    raise exc


@blueprint.route("/api/settings/workspaces", methods=["GET", "POST"])
def settings_workspaces_collection():
    require_settings_access()
    service = _service()
    if request.method == "GET":
        return jsonify(
            {
                **service.grid_metadata(),
                "client_size_summary": client_size_summary(),
                "widget_catalog": service.widget_catalog(),
                "workspaces": service.list_workspaces(),
            }
        )
    try:
        payload = _payload()
        source_id = payload.get("copy_from") or payload.get("source_id")
        if source_id:
            workspace = service.duplicate(
                str(source_id),
                name=payload.get("name"),
                workspace_id=payload.get("id"),
            )
        else:
            workspace = service.create_blank(
                payload.get("name"),
                workspace_id=payload.get("id"),
                kind="custom",
            )
        manifest = service.serialize(workspace)
    except (
        WorkspaceValidationError,
        WorkspaceNotFoundError,
        WorkspaceConflictError,
    ) as exc:
        return _handle_error(exc)
    _broadcast("created", workspace.id, workspace.revision)
    response = jsonify(manifest)
    response.status_code = 201
    response.headers["Location"] = f"/api/settings/workspaces/{workspace.id}"
    return response


@blueprint.post("/api/settings/workspaces/<workspace_id>/duplicate")
def duplicate_settings_workspace(workspace_id: str):
    require_settings_access()
    try:
        payload = _payload()
        workspace = _service().duplicate(
            workspace_id,
            name=payload.get("name"),
            workspace_id=payload.get("id"),
        )
        manifest = _service().serialize(workspace)
    except (
        WorkspaceValidationError,
        WorkspaceNotFoundError,
        WorkspaceConflictError,
    ) as exc:
        return _handle_error(exc)
    _broadcast("created", workspace.id, workspace.revision)
    response = jsonify(manifest)
    response.status_code = 201
    response.headers["Location"] = f"/api/settings/workspaces/{workspace.id}"
    return response


@blueprint.route(
    "/api/settings/workspaces/<workspace_id>",
    methods=["GET", "PUT", "DELETE"],
)
def settings_workspace_item(workspace_id: str):
    require_settings_access()
    service = _service()
    try:
        if request.method == "GET":
            return jsonify(service.serialize(workspace_id))
        payload = _payload()
        revision = _revision_from_request(payload)
        if request.method == "PUT":
            workspace = service.update(
                workspace_id,
                payload,
                expected_revision=revision,
            )
            _broadcast("updated", workspace.id, workspace.revision)
            return jsonify(service.serialize(workspace))
        deleted = service.delete(workspace_id, expected_revision=revision)
        _broadcast("deleted", deleted.id, deleted.revision)
        return jsonify({"ok": True, "workspace": service.serialize_summary(deleted)})
    except (
        WorkspaceValidationError,
        WorkspaceNotFoundError,
        WorkspaceConflictError,
    ) as exc:
        return _handle_error(exc)
