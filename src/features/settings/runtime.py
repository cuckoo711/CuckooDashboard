"""Settings 保存后的运行时配置刷新。"""

from __future__ import annotations

from typing import Any

from core.config import load_config
from providers import get_providers


def apply_runtime_config() -> tuple[list[str], list[str]]:
    """清理聚合缓存并动态调用所有 Provider 与服务的 reload_config。"""
    applied: list[str] = []
    errors: list[str] = []
    try:
        from services.dashboard_data_service import invalidate_dashboard_data_cache

        invalidate_dashboard_data_cache()
        applied.append("dashboard_data")
    except Exception as exc:
        errors.append(f"dashboard_data: {exc}")

    for provider_name, provider in get_providers().items():
        reload_fn = getattr(provider, "reload_config", None)
        if not callable(reload_fn):
            continue
        try:
            reload_fn()
            applied.append(provider_name)
        except Exception as exc:
            errors.append(f"{provider_name}: {exc}")

    hooks: list[tuple[str, Any]] = []
    try:
        from services.github_service import reload_config as reload_github

        hooks.append(("github", reload_github))
    except Exception as exc:
        errors.append(f"github: {exc}")
    try:
        from services.system_service import reload_config as reload_system

        hooks.append(("system", reload_system))
    except Exception as exc:
        errors.append(f"system: {exc}")
    try:
        from core.logging_config import reload_logging

        hooks.append(("logging", lambda: reload_logging(load_config())))
    except Exception as exc:
        errors.append(f"logging: {exc}")

    for name, hook in hooks:
        try:
            hook()
            applied.append(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return applied, errors
