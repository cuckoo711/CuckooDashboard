"""Public runtime catalog and same-origin assets for active extensions."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, send_file

from extensions.manager import ExtensionManagerError

blueprint = Blueprint("extensions", __name__)


def _manager():
    manager = current_app.extensions.get("extension_manager")
    if manager is None:
        abort(404)
    return manager


@blueprint.get("/api/runtime/extensions")
def runtime_extension_catalog():
    response = jsonify(_manager().runtime_catalog())
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.get("/runtime/extensions/<extension_id>/assets/<path:filename>")
def runtime_extension_asset(extension_id: str, filename: str):
    try:
        target = _manager().resolve_asset(extension_id, filename)
    except ExtensionManagerError:
        abort(404)
    response = send_file(target, conditional=True)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
