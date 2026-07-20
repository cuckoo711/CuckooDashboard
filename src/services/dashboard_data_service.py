"""Provider 无关的主看板数据聚合。"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from contracts.dashboard import DashboardAggregate, DashboardTotals, ProviderSnapshots, UsageSource
from contracts.provider import DailyUsage, ProviderCallOutcome, ProviderStatus
from core.cache import TTLCache
from providers import get_provider, get_providers

logger = logging.getLogger("cuckoo.dashboard_data")

_CACHE_TTL = 55
_cache = TTLCache(_CACHE_TTL)


def _provider_status(provider: Any) -> ProviderStatus:
    getter = getattr(provider, "get_status", None)
    if not callable(getter):
        return ProviderStatus.from_value({
            "status": "unknown", "ok": False, "enabled": True, "error": None,
        })
    try:
        value = getter()
        if isinstance(value, Mapping):
            return ProviderStatus.from_value(value)
        return ProviderStatus.from_value({
            "status": "unknown", "ok": False, "enabled": True, "error": None,
        })
    except Exception as exc:
        return ProviderStatus.from_value({
            "status": "error", "ok": False, "enabled": True, "error": str(exc),
        })


def _call(
    provider_id: str,
    provider: Any,
    method: str,
    *args: Any,
    **kwargs: Any,
) -> ProviderCallOutcome[Any]:
    fn = getattr(provider, method, None)
    if not callable(fn):
        return ProviderCallOutcome(provider=provider_id, called=False)
    try:
        return ProviderCallOutcome(provider=provider_id, data=fn(*args, **kwargs))
    except Exception as exc:
        logger.warning("[dashboard_data] %s.%s() 失败: %s", provider_id, method, exc)
        return ProviderCallOutcome(provider=provider_id, error=str(exc))


def _snapshot_vibe_methods(provider_id: str, provider: Any, snapshots: ProviderSnapshots) -> None:
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
        outcome = _call(provider_id, provider, method, *args)
        provider_snapshot[method] = outcome.data


def build_dashboard_aggregate(*, providers: Mapping[str, Any] | None = None) -> DashboardAggregate:
    """构建内部类型化 aggregate，便于对 fake Provider 做纯单元测试。"""
    active_providers = dict(providers) if isinstance(providers, Mapping) else get_providers()
    totals = DashboardTotals()
    snapshots: ProviderSnapshots = {}
    statuses: dict[str, ProviderStatus] = {}
    usage_sources: list[UsageSource] = []

    for provider_id, provider in sorted(active_providers.items(), key=lambda item: str(item[0]).casefold()):
        name = str(provider_id)
        capabilities = set(getattr(provider, "CAPABILITIES", ()) or ())
        statuses[name] = _provider_status(provider)
        _snapshot_vibe_methods(name, provider, snapshots)
        if "daily_usage" not in capabilities:
            continue

        outcome = _call(name, provider, "get_today_usage")
        snapshots.setdefault(name, {})["get_today_usage"] = outcome.data
        if not isinstance(outcome.data, Mapping):
            if outcome.error:
                usage_sources.append(UsageSource(provider=name, ok=False, error=outcome.error))
            continue

        usage = DailyUsage.from_value(outcome.data)
        totals.add(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            total_tokens=usage.total_tokens,
            uncached_input_tokens=usage.uncached_input_tokens,
        )
        usage_sources.append(UsageSource(
            provider=name,
            ok=True,
            source_count=usage.source_count,
            period=usage.period,
        ))

    return DashboardAggregate(
        success=True,
        timestamp=datetime.now(timezone.utc).isoformat(),
        today=totals,
        provider_statuses=statuses,
        usage_sources=usage_sources,
        snapshots=snapshots,
    )


def build_dashboard_data(*, providers: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """兼容入口：继续返回包含 ``_provider_snapshots`` 的原 dict。"""
    return build_dashboard_aggregate(providers=providers).to_compat_payload()


def fetch_dashboard_aggregate() -> DashboardAggregate:
    """返回带 TTL 缓存的内部类型化 Dashboard aggregate。"""
    cached = _cache.get()
    if isinstance(cached, DashboardAggregate):
        return copy.deepcopy(cached)
    result = build_dashboard_aggregate()
    _cache.set(copy.deepcopy(result))
    return result


def fetch_dashboard_data() -> dict[str, Any]:
    """兼容入口：继续返回包含私有快照的原 Dashboard dict。"""
    return fetch_dashboard_aggregate().to_compat_payload()


def invalidate_dashboard_data_cache() -> None:
    """设置/认证变更后清除聚合缓存。"""
    _cache.clear()


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
    outcome = _call(provider_id, provider, method, *args)
    if resource == "status" and isinstance(outcome.data, Mapping):
        return ProviderStatus.from_value(outcome.data).to_provider_payload()
    return outcome.data
