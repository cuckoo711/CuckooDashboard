"""Lightweight service health aggregation — 基于动态 Provider 插件发现。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from providers import get_providers
from services.github_service import get_github_status
from services.media_service import get_media_status
from services.system_service import get_system_status

logger = logging.getLogger("cuckoo.health")


def _normalize_status(value: Mapping[str, Any] | None) -> dict:
    """Ensure every service exposes the same health fields."""
    value = value if isinstance(value, Mapping) else {}
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


def _get_provider_health() -> dict[str, dict]:
    """自动发现所有 Provider，并按注册名返回其轻量状态。"""
    services: dict[str, dict] = {}
    for name, provider in sorted(get_providers().items(), key=lambda item: item[0].casefold()):
        get_status = getattr(provider, "get_status", None)
        if not callable(get_status):
            services[name] = _normalize_status({
                "status": "unknown",
                "ok": False,
                "error": "Provider 缺少 get_status()",
            })
            continue
        try:
            services[name] = _normalize_status(get_status())
        except Exception as exc:
            logger.warning("[health] %s.get_status() 调用失败: %s", name, exc)
            services[name] = _normalize_status({
                "status": "error",
                "ok": False,
                "error": str(exc),
            })
    return services


def get_health_snapshot() -> dict:
    """Return cached service statuses without triggering external refreshes."""
    services = _get_provider_health()
    services.update({
        "github": _normalize_status(get_github_status()),
        "system": _normalize_status(get_system_status()),
        "media": _normalize_status(get_media_status()),
    })
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
