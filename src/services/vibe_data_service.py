"""Vibe Coding 卡片的数据源选择与标准化。

配置位于 ``dashboard.vibe_coding``。本模块将 Provider 插件的原始数据转换为
前端所需的单一 payload，并把选择、排序和配置校验集中在后端，避免前端根据
不同消息到达顺序拼装同一张卡片。
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from typing import Any

from core.config import load_config
from providers import get_providers

logger = logging.getLogger("cuckoo.vibe_data")

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_BALANCE_COLOR = "#888888"
_MISSING = object()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _sort_text(value: Any) -> str:
    return str(value or "").casefold()


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_count(value: Any) -> int:
    return max(0, int(_as_number(value)))


def _to_percent(value: Any) -> float:
    """兼容 0–1 比例与 0–100 百分比两种 Provider 表示法。"""
    percent = _as_number(value)
    if 0 <= percent <= 1:
        percent *= 100
    return min(100.0, max(0.0, percent))


def _provider_entries(providers: Mapping[str, Any]) -> list[tuple[str, Any]]:
    return sorted(
        ((str(name), provider) for name, provider in providers.items()),
        key=lambda item: item[0].casefold(),
    )


def _has_capability(provider: Any, capability: str) -> bool:
    capabilities = getattr(provider, "CAPABILITIES", ())
    return capability in capabilities


def _find_provider(
    entries: list[tuple[str, Any]], requested: str | None,
) -> tuple[str, Any] | None:
    if not requested:
        return None
    requested_key = requested.casefold()
    return next((entry for entry in entries if entry[0].casefold() == requested_key), None)


def _call_provider(provider_name: str, provider: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    method_fn = getattr(provider, method, None)
    if not callable(method_fn):
        return None
    try:
        return method_fn(*args, **kwargs)
    except Exception as exc:  # Provider 失败不能影响其它 Vibe 数据源。
        logger.warning("[vibe] %s.%s() 调用失败: %s", provider_name, method, exc)
        return None


def _prefetched_or_call(
    provider_name: str,
    provider: Any,
    method: str,
    prefetched_provider_data: Mapping[str, Any] | None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """优先复用聚合器按 Provider/方法提供的通用快照。"""
    snapshots = _as_mapping(prefetched_provider_data)
    snapshot = snapshots.get(provider_name)
    if not isinstance(snapshot, Mapping):
        provider_key = provider_name.casefold()
        snapshot = next(
            (
                value for name, value in snapshots.items()
                if str(name).casefold() == provider_key and isinstance(value, Mapping)
            ),
            None,
        )
    if isinstance(snapshot, Mapping):
        prefetched = snapshot.get(method, _MISSING)
        if prefetched is not _MISSING:
            return prefetched
    return _call_provider(provider_name, provider, method, *args, **kwargs)


def _normalize_color(value: Any, index: int) -> str:
    color = _clean_string(value)
    if color and _COLOR_RE.fullmatch(color):
        return color
    if color:
        logger.warning(
            "[vibe] balances[%s].color 必须是 #RRGGBB，已使用默认颜色",
            index,
        )
    return _DEFAULT_BALANCE_COLOR


def build_vibe_coding_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """将 ``dashboard.vibe_coding`` 规范化为内部配置。

    ``balances`` 缺失或为空均表示不渲染 footer；环形图和模型条的 Provider
    选择为空时由运行时 Provider 列表决定默认来源。
    """
    dashboard = _as_mapping(_as_mapping(config).get("dashboard"))
    raw_vibe = dashboard.get("vibe_coding")
    vibe = _as_mapping(raw_vibe)
    if raw_vibe is not None and not isinstance(raw_vibe, Mapping):
        logger.warning("[vibe] dashboard.vibe_coding 必须是对象，已使用默认选择规则")

    ring = _as_mapping(vibe.get("ring"))
    model_bars = _as_mapping(vibe.get("model_bars"))

    raw_balances = vibe.get("balances", [])
    if raw_balances is None:
        raw_balances = []
    if not isinstance(raw_balances, list):
        logger.warning("[vibe] dashboard.vibe_coding.balances 必须是列表，已忽略")
        raw_balances = []

    balances: list[dict[str, str]] = []
    for index, raw_entry in enumerate(raw_balances):
        if not isinstance(raw_entry, Mapping):
            logger.warning("[vibe] balances[%s] 必须是对象，已忽略", index)
            continue

        enabled = raw_entry.get("enabled", True)
        if enabled is False:
            continue
        if not isinstance(enabled, bool):
            logger.warning("[vibe] balances[%s].enabled 必须是布尔值，已按启用处理", index)

        provider = _clean_string(raw_entry.get("provider"))
        if not provider:
            logger.warning("[vibe] balances[%s] 缺少 provider，已忽略", index)
            continue

        balances.append({
            "provider": provider,
            "name": _clean_string(raw_entry.get("name")) or provider,
            "color": _normalize_color(raw_entry.get("color"), index),
        })

    # 用户要求无需人工决定：按稳定键排序并去重，同一 Provider 只显示一次。
    balances.sort(key=lambda entry: (entry["provider"].casefold(), entry["name"].casefold()))
    unique_balances: list[dict[str, str]] = []
    seen_providers: set[str] = set()
    for entry in balances:
        provider_key = entry["provider"].casefold()
        if provider_key in seen_providers:
            logger.warning("[vibe] 余额 Provider %s 重复，已忽略后续配置", entry["provider"])
            continue
        seen_providers.add(provider_key)
        unique_balances.append(entry)

    return {
        "ring": {
            "provider": _clean_string(ring.get("provider")),
            "item": _clean_string(ring.get("item")),
        },
        "model_bars": {
            "provider": _clean_string(model_bars.get("provider")),
        },
        "balances": unique_balances,
    }


def _ring_candidates(
    entries: list[tuple[str, Any]], requested: str | None,
) -> list[tuple[str, Any]]:
    selected = _find_provider(entries, requested)
    if selected is not None:
        if _has_capability(selected[1], "token_plan") and callable(getattr(selected[1], "get_plan_usage", None)):
            return [selected]
        logger.warning("[vibe] 环形图 Provider %s 不支持 token_plan，已回退到默认来源", requested)
    elif requested:
        logger.warning("[vibe] 未找到环形图 Provider %s，已回退到默认来源", requested)

    return [
        entry for entry in entries
        if _has_capability(entry[1], "token_plan")
        and callable(getattr(entry[1], "get_plan_usage", None))
    ]


def _plan_item_sort_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        _sort_text(item.get(key))
        for key in ("id", "planId", "planCode", "name")
    )


def _plan_item_matches(item: Mapping[str, Any], requested: str) -> bool:
    requested_key = requested.casefold()
    return any(
        _sort_text(item.get(key)) == requested_key
        for key in ("id", "planId", "planCode", "name")
    )


def _extract_plan_item(usage: Any, requested: str | None) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    data = _as_mapping(usage)
    group = _as_mapping(data.get("monthUsage"))
    if not group:
        # 兼容实现标准接口的第三方 Provider：直接返回 {items, percent}。
        group = data

    raw_items = group.get("items", [])
    items = [item for item in raw_items if isinstance(item, Mapping)] if isinstance(raw_items, list) else []
    if not items and any(key in group for key in ("used", "limit", "percent")):
        items = [group]
    if not items:
        return None

    items.sort(key=_plan_item_sort_key)
    if requested:
        matched = [item for item in items if _plan_item_matches(item, requested)]
        if matched:
            return matched[0], group
        logger.warning("[vibe] 未找到 Token Plan 项 %s，已选择排序后的第一项", requested)
    return items[0], group


def _build_ring(
    ring_config: Mapping[str, Any],
    entries: list[tuple[str, Any]],
    prefetched_provider_data: Mapping[str, Any] | None,
) -> dict[str, Any]:
    candidates = _ring_candidates(entries, ring_config.get("provider"))
    fallback_provider = candidates[0][0] if candidates else None

    for provider_name, provider in candidates:
        usage = _prefetched_or_call(
            provider_name,
            provider,
            "get_plan_usage",
            prefetched_provider_data,
        )
        selected = _extract_plan_item(usage, ring_config.get("item"))
        if selected is None:
            continue

        item, group = selected
        used = _as_number(item.get("used"))
        limit = _as_number(item.get("limit"))
        percent = (used / limit * 100) if limit > 0 else _to_percent(item.get("percent", group.get("percent", 0)))
        item_name = (
            _clean_string(item.get("name"))
            or _clean_string(item.get("planCode"))
            or _clean_string(item.get("id"))
            or provider_name
        )
        return {
            "available": True,
            "provider": provider_name,
            "item": item_name,
            "percent": min(100.0, max(0.0, percent)),
            "used": used,
            "limit": limit,
        }

    return {
        "available": False,
        "provider": fallback_provider,
        "item": None,
        "percent": 0.0,
        "used": 0.0,
        "limit": 0.0,
    }


def _rows_from_payload(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("data", "rows", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                value = nested
                break
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _normalize_model_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _rows_from_payload(value):
        label = _clean_string(row.get("model")) or _clean_string(row.get("name"))
        if not label:
            continue
        rows.append({
            "label": label,
            "value": _as_number(row.get("totalToken", row.get("tokens", 0))),
            "requests": _as_count(row.get("requestCount", row.get("requests", 0))),
        })
    return rows


def _normalize_channel_rows(value: Any) -> tuple[str, str | None, list[dict[str, Any]]]:
    """标准化渠道明细，并从数据字段而非 Provider 名推断度量类型。"""
    payload = _as_mapping(value)
    currency = _clean_string(payload.get("currency")) or _clean_string(payload.get("currencyCode"))
    rows: list[dict[str, Any]] = []
    has_cost_metric = False
    cost_keys = ("cost", "totalCost", "totalQuotaCost")

    for row in _rows_from_payload(value):
        label = (
            _clean_string(row.get("groupKey"))
            or _clean_string(row.get("channel"))
            or _clean_string(row.get("name"))
        )
        if not label:
            continue

        raw_value = _MISSING
        for key in cost_keys:
            if key in row:
                raw_value = row[key]
                has_cost_metric = True
                break
        if raw_value is _MISSING:
            raw_value = row.get("totalToken", row.get("tokens", 0))

        if not currency:
            currency = _clean_string(row.get("currency")) or _clean_string(row.get("currencyCode"))
        rows.append({
            "label": label,
            "value": _as_number(raw_value),
            "requests": _as_count(row.get("requestCount", row.get("requests", 0))),
        })

    return ("currency" if has_cost_metric else "tokens"), currency, rows


def _model_candidates(
    entries: list[tuple[str, Any]], requested: str | None,
) -> list[tuple[str, Any]]:
    def supports_model_data(entry: tuple[str, Any]) -> bool:
        provider = entry[1]
        return callable(getattr(provider, "get_model_breakdown", None)) or callable(
            getattr(provider, "get_channel_breakdown", None)
        )

    selected = _find_provider(entries, requested)
    if selected is not None:
        if supports_model_data(selected):
            return [selected]
        logger.warning("[vibe] 模型条 Provider %s 不支持模型或渠道明细，已回退到默认来源", requested)
    elif requested:
        logger.warning("[vibe] 未找到模型条 Provider %s，已回退到默认来源", requested)

    # 无配置时不偏向任何内置 Provider，直接使用注册名的稳定顺序。
    return [entry for entry in entries if supports_model_data(entry)]


def _build_model_bars(
    model_config: Mapping[str, Any],
    entries: list[tuple[str, Any]],
    prefetched_provider_data: Mapping[str, Any] | None,
) -> dict[str, Any]:
    candidates = _model_candidates(entries, model_config.get("provider"))
    fallback_provider = candidates[0][0] if candidates else None

    for provider_name, provider in candidates:
        model_data = _prefetched_or_call(
            provider_name,
            provider,
            "get_model_breakdown",
            prefetched_provider_data,
        )
        model_rows = _normalize_model_rows(model_data)
        if model_rows:
            return {
                "available": True,
                "provider": provider_name,
                "kind": "tokens",
                "currency": None,
                "rows": model_rows,
            }

        channel_data = _prefetched_or_call(
            provider_name,
            provider,
            "get_channel_breakdown",
            prefetched_provider_data,
            days=7,
        )
        channel_kind, channel_currency, channel_rows = _normalize_channel_rows(channel_data)
        if channel_rows:
            return {
                "available": True,
                "provider": provider_name,
                "kind": channel_kind,
                "currency": channel_currency,
                "rows": channel_rows,
            }

    return {
        "available": False,
        "provider": fallback_provider,
        "kind": None,
        "currency": None,
        "rows": [],
    }


def _build_balances(
    balance_configs: list[dict[str, str]],
    entries: list[tuple[str, Any]],
    prefetched_provider_data: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    balances: list[dict[str, str]] = []
    for entry in balance_configs:
        selected = _find_provider(entries, entry["provider"])
        if selected is None:
            logger.warning("[vibe] 未找到余额 Provider %s，已忽略", entry["provider"])
            continue
        provider_name, provider = selected
        if not _has_capability(provider, "balance") or not callable(getattr(provider, "get_balance", None)):
            logger.warning("[vibe] 余额 Provider %s 不支持 balance，已忽略", provider_name)
            continue

        raw_balance = _prefetched_or_call(
            provider_name,
            provider,
            "get_balance",
            prefetched_provider_data,
        )
        balance = _as_mapping(raw_balance)
        amount = balance.get("balance", _MISSING)
        if amount is _MISSING or amount is None:
            continue

        balances.append({
            "provider": provider_name,
            "name": entry["name"],
            "color": entry["color"],
            "currency": _clean_string(balance.get("currency")) or "",
            "balance": str(amount),
        })
        if len(balances) == 2:
            break
    return balances


def get_vibe_data(
    prefetched_provider_data: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    providers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 Vibe Coding 卡片的统一数据 payload。

    ``config`` 和 ``providers`` 为可选注入参数，便于在不触发网络请求的情况下
    做配置/选择器单测；运行时默认读取热重载 YAML 和自动发现的 Provider 注册表。
    """
    active_config = config if config is not None else load_config()
    active_providers = providers if providers is not None else get_providers()
    if not isinstance(active_providers, Mapping):
        active_providers = {}

    normalized = build_vibe_coding_config(active_config)
    entries = _provider_entries(active_providers)
    return {
        "ring": _build_ring(normalized["ring"], entries, prefetched_provider_data),
        "model_bars": _build_model_bars(normalized["model_bars"], entries, prefetched_provider_data),
        "balances": _build_balances(normalized["balances"], entries, prefetched_provider_data),
    }
