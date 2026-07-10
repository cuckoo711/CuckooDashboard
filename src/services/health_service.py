"""Lightweight service health aggregation."""

from __future__ import annotations

from datetime import datetime, timezone

from services.github_service import get_github_status
from services.local_platform_service import get_local_platform_status
from services.media_service import get_media_status
from services.mimo_service import get_mimo_status
from services.nug_service import get_nug_status
from services.system_service import get_system_status


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
        "mimo": _normalize_status(get_mimo_status()),
        "github": _normalize_status(get_github_status()),
        "nug": _normalize_status(get_nug_status()),
        "system": _normalize_status(get_system_status()),
        "media": _normalize_status(get_media_status()),
        "local_platforms": _normalize_status(get_local_platform_status()),
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
