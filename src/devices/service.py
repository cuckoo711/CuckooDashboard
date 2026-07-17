"""Validation and access policy for persistent display terminals."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from devices.repository import DeviceRepository

_DEVICE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_STATUSES = {"pending", "approved", "disabled"}
_SCALE_MODES = {"auto", "fixed"}
_MAX_NOTE_LENGTH = 500
_MAX_NAME_LENGTH = 120


class DeviceValidationError(ValueError):
    """A device payload does not meet the public terminal contract."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field

    def as_dict(self) -> dict[str, str]:
        payload = {"message": str(self)}
        if self.field:
            payload["field"] = self.field
        return payload


class DeviceService:
    """Own browser-terminal registration, approval and configuration policy."""

    def __init__(
        self,
        repository: DeviceRepository,
        *,
        workspace_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_exists = workspace_exists or (lambda _workspace_id: True)

    @staticmethod
    def normalize_device_id(value: Any) -> str:
        device_id = str(value or "").strip().lower()
        if not _DEVICE_ID_RE.fullmatch(device_id):
            raise DeviceValidationError("device_id 必须是 UUID v4/v5", "device_id")
        return device_id

    @staticmethod
    def _text(value: Any, *, field: str, maximum: int) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise DeviceValidationError(f"{field} 必须是字符串", field)
        value = value.strip()
        if len(value) > maximum:
            raise DeviceValidationError(f"{field} 不能超过 {maximum} 个字符", field)
        return value

    @staticmethod
    def _viewport(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise DeviceValidationError("viewport 必须是对象", "viewport")
        return dict(value)

    def register(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise DeviceValidationError("请求体必须是对象")
        device_id = self.normalize_device_id(payload.get("device_id"))
        display_name = self._text(payload.get("display_name"), field="display_name", maximum=_MAX_NAME_LENGTH)
        page = self._text(payload.get("page"), field="page", maximum=40)
        viewport = self._viewport(payload.get("viewport"))
        return self.repository.register_or_touch(
            device_id,
            display_name=display_name,
            page=page,
            viewport=viewport,
        )

    def get(self, device_id: Any) -> dict[str, Any] | None:
        return self.repository.get(self.normalize_device_id(device_id))

    def list_devices(self) -> list[dict[str, Any]]:
        return self.repository.list()

    def update(self, device_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise DeviceValidationError("请求体必须是对象")
        normalized_id = self.normalize_device_id(device_id)
        changes: dict[str, Any] = {}
        if "status" in payload:
            status = str(payload["status"] or "").strip().lower()
            if status not in _STATUSES:
                raise DeviceValidationError("status 必须是 pending、approved 或 disabled", "status")
            changes["status"] = status
        if "workspace_id" in payload:
            workspace_id = self._text(payload["workspace_id"], field="workspace_id", maximum=120)
            if not workspace_id:
                raise DeviceValidationError("workspace_id 不能为空", "workspace_id")
            if not self.workspace_exists(workspace_id):
                raise DeviceValidationError("目标工作区不存在", "workspace_id")
            changes["workspace_id"] = workspace_id
        if "scale_mode" in payload:
            scale_mode = str(payload["scale_mode"] or "").strip().lower()
            if scale_mode not in _SCALE_MODES:
                raise DeviceValidationError("scale_mode 必须是 auto 或 fixed", "scale_mode")
            changes["scale_mode"] = scale_mode
        if "scale" in payload:
            try:
                scale = float(payload["scale"])
            except (TypeError, ValueError) as exc:
                raise DeviceValidationError("scale 必须是数字", "scale") from exc
            if not 0.25 <= scale <= 4.0:
                raise DeviceValidationError("scale 必须在 0.25 到 4.0 之间", "scale")
            changes["scale"] = round(scale, 3)
        if "note" in payload:
            changes["note"] = self._text(payload["note"], field="note", maximum=_MAX_NOTE_LENGTH)
        if "display_name" in payload:
            changes["display_name"] = self._text(
                payload["display_name"], field="display_name", maximum=_MAX_NAME_LENGTH
            )
        if "layout_override" in payload:
            layout_override = payload["layout_override"]
            if not isinstance(layout_override, Mapping):
                raise DeviceValidationError("layout_override 必须是对象", "layout_override")
            changes["layout_override"] = dict(layout_override)
        device = self.repository.update(normalized_id, **changes)
        if device is None:
            raise DeviceValidationError("终端不存在", "device_id")
        return device

    def session_payload(self, device: Mapping[str, Any]) -> dict[str, Any]:
        approved = device.get("status") == "approved"
        return {
            "device": dict(device),
            "approved": approved,
            "reason": None if approved else (
                "device_disabled" if device.get("status") == "disabled" else "device_pending"
            ),
            "workspace_id": device.get("workspace_id") if approved else None,
            "scale_mode": device.get("scale_mode") if approved else None,
            "scale": device.get("scale") if approved else None,
            "layout_override": device.get("layout_override") if approved else {},
        }

    def can_access_workspace(self, device_id: Any, workspace_id: str) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            device = self.get(device_id)
        except DeviceValidationError:
            return False, "device_required", None
        if device is None:
            return False, "device_pending", None
        if device.get("status") != "approved":
            return False, "device_disabled" if device.get("status") == "disabled" else "device_pending", device
        if str(device.get("workspace_id") or "main") != str(workspace_id):
            return False, "workspace_not_assigned", device
        return True, "", device
