"""服务健康状态的稳定类型化契约。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceHealth:
    """与 health_service 既有 ``_normalize_status`` 七键输出完全一致。"""

    status: Any = "unknown"
    ok: bool = False
    enabled: bool = True
    stale: bool = False
    error: Any = None
    last_success_at: Any = None
    details: Any = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | None) -> "ServiceHealth":
        raw = value if isinstance(value, Mapping) else {}
        status = raw.get("status", "unknown")
        return cls(
            status=status,
            ok=bool(raw.get("ok", status == "ok")),
            enabled=bool(raw.get("enabled", True)),
            stale=bool(raw.get("stale", status == "stale")),
            error=raw.get("error"),
            last_success_at=raw.get("last_success_at"),
            details=raw.get("details") or {},
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "enabled": self.enabled,
            "stale": self.stale,
            "error": self.error,
            "last_success_at": self.last_success_at,
            "details": self.details,
        }
