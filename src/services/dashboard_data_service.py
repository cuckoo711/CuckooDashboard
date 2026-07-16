"""Provider 无关的主看板数据聚合。"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from core.cache import TTLCache
from providers import get_provider, get_providers

logger = logging.getLogger("cuckoo.dashboard_data")

_CACHE_TTL = 55
_cache = TTLCache(_CACHE_TTL)
_last_result: dict[str, Any] | None = None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _provider_status(provider: Any) -> dict[str, Any]:
    getter = getattr(provider, "get_status", None)
    if not callable(getter):
        return {"status": "unknown", "ok": False, "enabled": True, "error": None}
    try:
        value = getter()
        return dict(value) if isinstance(value, Mapping) else {"status": "unknown", "ok": False, "enabled": True, "error": None}
    except Exception as exc:
        return {"status": "error", "ok": False, "enabled": True, "error": str(exc)}


def _call(provider_id: str, provider: Any, method: str, *args: Any, **kwargs: Any) -> tuple[Any, str | None]:
    fn = getattr(provider, method, None)
    if not callable(fn):
        return None, None
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        logger.warning("[dashboard_data] %s.%s() 失败: %s", provider_id, method, exc)
        return None, str(exc)


def _snapshot_vibe_methods(provider_id: str, provider: Any, snapshots: dict[str, dict[str, Any]]) -> None:
    """按 capability 预取 Vibe 可能消费的方法，不理解任何 Provider 专属数据。"""
    capabilities = set(getattr(provider, "CAPABILITIES", ()) or ())
    methods: list[tuple[str, tuple[Any, ...]]] = []
    if "token_plan" in capabilities:
        methods.append(("get_plan_usage", ()))
    if callable(getattr(provider, "get_model_breakdown", None)):
        methods.append(("get_model_breakdown", ()))
    elif callable(getattr(provider, "get_channel_breakdown", None)):
        methods.append(("get_channel_breakdown", ()))
    if "balance" in capabilities:
        methods.append(("get_balance", ()))
    if not methods:
        return

    provider_snapshot = snapshots.setdefault(provider_id, {})
    for method, args in methods:
        value, _ = _call(provider_id, provider, method, *args)
        provider_snapshot[method] = value


def build_dashboard_data(*, providers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """构建统一 Dashboard payload，便于对 fake Provider 做纯单元测试。"""
    active_providers = dict(providers) if isinstance(providers, Mapping) else get_providers()
    today = {
        "in": 0,
        "out": 0,
        "cache": 0,
        "total": 0,
        "inMiss": 0,
    }
    snapshots: dict[str, dict[str, Any]] = {}
    statuses: dict[str, dict[str, Any]] = {}
    usage_sources: list[dict[str, Any]] = []

    for provider_id, provider in sorted(active_providers.items(), key=lambda item: str(item[0]).casefold()):
        capabilities = set(getattr(provider, "CAPABILITIES", ()) or ())
        statuses[str(provider_id)] = _provider_status(provider)
        _snapshot_vibe_methods(str(provider_id), provider, snapshots)
        if "daily_usage" not in capabilities:
            continue

        usage, error = _call(str(provider_id), provider, "get_today_usage")
        snapshots.setdefault(str(provider_id), {})["get_today_usage"] = usage
        if not isinstance(usage, Mapping):
            if error:
                usage_sources.append({"provider": str(provider_id), "ok": False, "error": error})
            continue

        input_tokens = _count(usage.get("input_tokens"))
        output_tokens = _count(usage.get("output_tokens"))
        cached_input_tokens = _count(usage.get("cached_input_tokens"))
        total_tokens = _count(usage.get("total_tokens"))
        uncached_input_tokens = _count(usage.get("uncached_input_tokens"))
        if not uncached_input_tokens:
            uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
        today["in"] += input_tokens
        today["out"] += output_tokens
        today["cache"] += cached_input_tokens
        today["total"] += total_tokens
        today["inMiss"] += uncached_input_tokens
        usage_sources.append({
            "provider": str(provider_id),
            "ok": True,
            "source_count": _count(usage.get("source_count")),
            "period": str(usage.get("period") or "today"),
        })

    return {
        "success": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "today": today,
        "provider_statuses": statuses,
        "usage_sources": usage_sources,
        "_provider_snapshots": snapshots,
    }


def fetch_dashboard_data() -> dict[str, Any]:
    """返回带 TTL 缓存的 Provider 无关 Dashboard 数据。"""
    global _last_result
    cached = _cache.get()
    if cached is not None:
        return copy.deepcopy(cached)
    result = build_dashboard_data()
    _last_result = copy.deepcopy(result)
    _cache.set(copy.deepcopy(result))
    return result


def invalidate_dashboard_data_cache() -> None:
    """设置/认证变更后清除聚合缓存。"""
    global _last_result
    _cache.clear()
    _last_result = None


def get_provider_public_data(provider_id: str, resource: str, *, days: int = 7) -> Any:
    """为通用 Provider API 调用一组明确、安全的公开数据方法。"""
    provider = get_provider(provider_id)
    if provider is None:
        return None
    capabilities = set(getattr(provider, "CAPABILITIES", ()) or ())
    resources: dict[str, tuple[str | None, str, tuple[Any, ...]]] = {
        "status": (None, "get_status", ()),
        "today": ("daily_usage", "get_today_usage", ()),
        "balance": ("balance", "get_balance", ()),
        "usage": ("api_usage", "get_usage_summary", ()),
        "channels": ("api_usage", "get_channel_breakdown", (days,)),
        "plan": ("token_plan", "get_plan_usage", ()),
    }
    selected = resources.get(resource)
    if selected is None:
        return None
    capability, method, args = selected
    if capability is not None and capability not in capabilities:
        return None
    value, _ = _call(provider_id, provider, method, *args)
    return value
