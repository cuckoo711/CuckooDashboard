"""Loopback-only extension desired-state management for Settings."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from extensions.manager import ExtensionManagerError
from features.settings.security import require_settings_access

blueprint = Blueprint("settings_extensions", __name__)


def _manager():
    manager = current_app.extensions.get("extension_manager")
    if manager is None:
        raise ExtensionManagerError("extensions_unavailable", "extension manager is unavailable")
    return manager


def _error(exc: ExtensionManagerError):
    status = 409 if exc.code in {
        "extension_locked",
        "extension_not_loadable",
        "extension_in_use",
        "extension_has_dependents",
        "extension_conflict",
    } else 400
    return jsonify(
        {
            "error": {
                "code": exc.code,
                "message": str(exc),
                **exc.details,
            }
        }
    ), status


@blueprint.get("/api/settings/extensions")
def settings_extensions_collection():
    require_settings_access()
    try:
        return jsonify(_manager().list_extensions(include_paths=True))
    except ExtensionManagerError as exc:
        return _error(exc)


@blueprint.put("/api/settings/extensions/<extension_id>")
def update_settings_extension(extension_id: str):
    require_settings_access()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(
            {"error": {"code": "invalid_request", "message": "request body must be an object"}}
        ), 400
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return jsonify(
            {"error": {"code": "invalid_revision", "message": "revision must be a non-negative integer"}}
        ), 400
    try:
        extension = _manager().set_desired(
            extension_id,
            payload.get("desired_enabled"),
            expected_revision=revision,
        )
        return jsonify(
            {
                "revision": _manager().state_revision,
                "extension": extension,
            }
        )
    except ExtensionManagerError as exc:
        return _error(exc)


@blueprint.post("/api/settings/extensions/rescan")
def rescan_settings_extensions():
    require_settings_access()
    try:
        extensions = _manager().rescan()
        return jsonify(
            {"revision": _manager().state_revision, "extensions": extensions}
        )
    except ExtensionManagerError as exc:
        return _error(exc)
