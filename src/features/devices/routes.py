"""Public and Settings routes for browser display terminals."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.security import require_post_protection
from devices.service import DeviceValidationError
from features.settings.security import require_settings_access
from runtime.lifecycle import get_runtime

public_blueprint = Blueprint("devices_public", __name__)
settings_blueprint = Blueprint("devices_settings", __name__)


def _device_service():
    return current_app.extensions["device_service"]


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@public_blueprint.post("/api/device/session")
def api_device_session():
    """Register or refresh a browser terminal and return its access grant."""
    require_post_protection()
    try:
        device = _device_service().register(request.get_json(silent=True) or {})
    except DeviceValidationError as exc:
        return _no_store(jsonify({"error": exc.as_dict()})), 400
    payload = _device_service().session_payload(device)
    return _no_store(jsonify(payload))


@settings_blueprint.get("/api/settings/devices")
def api_settings_devices():
    """List registered display terminals with online connection summary."""
    require_settings_access()
    devices = _device_service().list_devices()
    clients = []
    try:
        clients = get_runtime().hub.list_clients()
    except Exception:
        clients = []
    online_by_device: dict[str, list[dict]] = {}
    for client in clients if isinstance(clients, list) else []:
        if not isinstance(client, dict):
            continue
        device_id = str(client.get("device_id") or "").strip().lower()
        if not device_id:
            continue
        online_by_device.setdefault(device_id, []).append(client)
    rows = []
    for device in devices:
        sessions = online_by_device.get(device["id"], [])
        rows.append({
            **device,
            "online": bool(sessions),
            "session_count": len(sessions),
            "sessions": sessions,
        })
    return _no_store(jsonify({"devices": rows}))


@settings_blueprint.route("/api/settings/devices/<device_id>", methods=["PATCH", "PUT", "DELETE"])
def api_settings_device_item(device_id: str):
    """Approve, disable, reconfigure or delete one display terminal."""
    require_settings_access()
    if request.method == "DELETE":
        try:
            device = _device_service().delete(device_id)
        except DeviceValidationError as exc:
            status = 404 if str(exc) == "终端不存在" else 400
            return _no_store(jsonify({"error": exc.as_dict()})), status
        try:
            get_runtime().hub.broadcast({
                "type": "device_updated",
                "data": {
                    "approved": False,
                    "reason": "device_deleted",
                    "device": device,
                    "workspace_id": None,
                    "scale_mode": None,
                    "scale": None,
                    "layout_override": {},
                },
                "device_id": device["id"],
            })
        except Exception:
            pass
        return _no_store(jsonify({"ok": True, "deleted": True, "device": device}))

    payload = request.get_json(silent=True) or {}
    try:
        device = _device_service().update(device_id, payload)
    except DeviceValidationError as exc:
        status = 404 if str(exc) == "终端不存在" else 400
        return _no_store(jsonify({"error": exc.as_dict()})), status
    session = _device_service().session_payload(device)
    try:
        hub = get_runtime().hub
        hub.broadcast({
            "type": "device_updated",
            "data": session,
            "device_id": device["id"],
        })
    except Exception:
        pass
    return _no_store(jsonify({"ok": True, **session}))
