"""Helpers that gate public dashboard access by display-terminal approval."""

from __future__ import annotations

from typing import Any

from flask import current_app, request

from devices.service import DeviceValidationError


def extract_device_id() -> str | None:
    """Read the browser device id from headers or common request fields."""
    header = request.headers.get("X-Dashboard-Device") or request.headers.get("X-Device-Id")
    if header:
        return str(header).strip()
    if request.method in {"POST", "PUT", "PATCH"}:
        payload = request.get_json(silent=True) or {}
        value = payload.get("device_id")
        if value:
            return str(value).strip()
    value = request.args.get("device_id")
    return str(value).strip() if value else None


def require_device_access(workspace_id: str | None = None) -> tuple[bool, dict[str, Any], int]:
    """Validate the caller against the registered display-terminal policy.

    Returns ``(ok, payload, status_code)``. When ``ok`` is true, ``payload``
    contains the approved device row.
    """
    service = current_app.extensions["device_service"]
    device_id = extract_device_id()
    if not device_id:
        return False, {
            "error": {
                "code": "device_required",
                "message": "缺少浏览器终端标识，请先完成设备登记",
            }
        }, 403
    try:
        device = service.get(device_id)
    except DeviceValidationError as exc:
        # 与“缺少设备标识”保持同一 error code 和同一状态码，客户端只需处理一种契约。
        return False, {"error": {**exc.as_dict(), "code": "device_required"}}, 403
    if device is None:
        return False, {
            "error": {
                "code": "device_pending",
                "message": "终端尚未登记，请先调用设备握手",
            }
        }, 403
    if device.get("status") != "approved":
        code = "device_disabled" if device.get("status") == "disabled" else "device_pending"
        message = "终端已被禁用" if code == "device_disabled" else "终端等待管理员在 Settings 中审批"
        return False, {
            "error": {
                "code": code,
                "message": message,
            },
            "device": device,
            "approved": False,
        }, 403
    if workspace_id is not None:
        assigned = str(device.get("workspace_id") or "main")
        if assigned != str(workspace_id):
            return False, {
                "error": {
                    "code": "workspace_not_assigned",
                    "message": "该终端未被分配到此工作区",
                    "workspace_id": assigned,
                },
                "device": device,
            }, 403
    return True, {"device": device, "approved": True}, 200
