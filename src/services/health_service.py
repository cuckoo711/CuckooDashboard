"""Lightweight service health aggregation — 基于 providers 插件体系。"""

from __future__ import annotations

from datetime import datetime, timezone

from services.github_service import get_github_status
from services.media_service import get_media_status
from services.system_service import get_system_status
import providers.mimo
import providers.nug
import providers.local_platform


def _normalize_status(value: dict) -> dict:
    """Ensure every service exposes the same health fields."""
    status = value.get("status", "unknown")
    return {
        "status": status,
        "ok": bool(value.get("ok", status == "ok")),
        "enabled": bool(value.get("enabled", True)),
        "stale": bool(value.get("stale", status == "stale")),
        "error": value.get("error"),
        "last_success_at": value.get("last_success_at"),
        "details": value.get("details") or {},
    }


def get_health_snapshot() -> dict:
    """Return cached service statuses without triggering external refreshes."""
    services = {
        "mimo": _normalize_status(providers.mimo.get_status()),
        "github": _normalize_status(get_github_status()),
        "nug": _normalize_status(providers.nug.get_status()),
        "system": _normalize_status(get_system_status()),
        "media": _normalize_status(get_media_status()),
        "local_platforms": _normalize_status(providers.local_platform.get_status()),
    }
    statuses = {item["status"] for item in services.values()}
    if "error" in statuses:
        overall = "error"
    elif statuses <= {"ok"}:
        overall = "ok"
    else:
        overall = "degraded"
    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": services,
    }
