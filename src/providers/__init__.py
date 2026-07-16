"""Provider Registry：自动发现、Schema 校验与能力调用。

本模块不包含任何 Dashboard 业务逻辑，也不识别任何内置 Provider。看板聚合位于
``services.dashboard_data_service``，认证和公共路由都通过 Provider hook 动态注册。
"""

from __future__ import annotations

import copy
import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

from contracts.provider import ProviderCallOutcome
from providers.auth import refresh_scheduler

logger = logging.getLogger("cuckoo.providers")

_PROVIDERS_DIR = Path(__file__).parent
_registry: dict[str, ModuleType] = {}
_discovered = False
_CONFIG_FIELD_TYPES = {
    "boolean", "string", "secret", "url", "integer", "number", "select", "color", "time",
    "string_list", "object_list", "key_value_map",
}
CAPABILITY_METHODS: dict[str, tuple[str, ...]] = {
    "token_plan": ("get_plan_detail", "get_plan_usage", "get_daily_detail", "get_model_breakdown"),
    "balance": ("get_balance",),
    "api_usage": ("get_usage_summary", "get_channel_breakdown"),
    "daily_usage": ("get_today_usage",),
}


def _warn_incomplete_capabilities(provider_id: str, provider: ModuleType) -> None:
    """报告声明能力与方法矩阵不一致，但绝不拒绝加载第三方插件。"""
    missing: list[str] = []
    if not callable(getattr(provider, "get_status", None)):
        missing.append("get_status")
    for capability in getattr(provider, "CAPABILITIES", ()) or ():
        for method in CAPABILITY_METHODS.get(str(capability), ()):
            if not callable(getattr(provider, method, None)):
                missing.append(f"{capability}.{method}")
    if missing:
        logger.warning(
            "[providers] 插件 %s capability-method 不完整: %s；仍继续加载",
            provider_id,
            ", ".join(missing),
        )


def _valid_schema_fields(fields: object, path: str) -> bool:
    if not isinstance(fields, list):
        return False
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            logger.warning("[providers] %s[%s] 必须是对象", path, index)
            return False
        key = field.get("key")
        field_type = field.get("type")
        if not isinstance(key, str) or not key.strip() or field_type not in _CONFIG_FIELD_TYPES:
            logger.warning("[providers] %s[%s] 字段定义无效", path, index)
            return False
        if field_type == "object_list":
            identity_key = field.get("identity_key")
            if not isinstance(identity_key, str) or not identity_key.strip():
                logger.warning("[providers] %s[%s] object_list 缺少 identity_key", path, index)
                return False
            if not _valid_schema_fields(field.get("item_fields"), f"{path}[{index}].item_fields"):
                return False
    return True


def _discover() -> None:
    """扫描 ``providers/`` 下的子包并注册声明了 capabilities 的插件。"""
    global _discovered
    if _discovered:
        return
    _discovered = True

    for item in sorted(_PROVIDERS_DIR.iterdir()):
        if not item.is_dir() or not (item / "__init__.py").exists() or item.name.startswith("_"):
            continue
        provider_id = item.name
        try:
            module = importlib.import_module(f"providers.{provider_id}")
            if not hasattr(module, "CAPABILITIES"):
                logger.warning("[providers] 插件 %s 缺少 CAPABILITIES 声明，已跳过", provider_id)
                continue
            declared_id = getattr(module, "PROVIDER_ID", None)
            if not isinstance(declared_id, str) or declared_id != provider_id:
                logger.warning("[providers] %s 必须声明且保持 PROVIDER_ID 与目录名一致，已跳过", provider_id)
                continue
            _warn_incomplete_capabilities(provider_id, module)
            _registry[provider_id] = module
            refresh_scheduler.register_provider(provider_id, module)
            logger.info("[providers] 已加载插件: %s -> %s", provider_id, module.CAPABILITIES)
        except Exception as exc:
            logger.error("[providers] 加载插件 %s 失败: %s", provider_id, exc)


def get_providers() -> dict[str, ModuleType]:
    """返回所有已注册插件的副本。"""
    _discover()
    return dict(_registry)


def get_provider(provider_id: str) -> ModuleType | None:
    """按稳定 Provider ID 查找插件。"""
    _discover()
    return _registry.get(provider_id)


def get_provider_config_schemas() -> list[dict[str, Any]]:
    """返回全部有效 Provider Schema，按 order/name 稳定排序。"""
    schemas: list[dict[str, Any]] = []
    for provider_id, provider in sorted(get_providers().items(), key=lambda item: item[0].casefold()):
        raw_schema = getattr(provider, "CONFIG_SCHEMA", None)
        if raw_schema is None:
            continue
        if not isinstance(raw_schema, dict):
            logger.warning("[providers] %s.CONFIG_SCHEMA 必须是对象，已跳过", provider_id)
            continue
        schema = copy.deepcopy(raw_schema)
        config_key = schema.get("config_key", provider_id)
        fields = schema.get("fields", [])
        if config_key != provider_id:
            logger.warning("[providers] %s 的 config_key 必须等于 provider_id，已跳过", provider_id)
            continue
        if not isinstance(fields, list) or not _valid_schema_fields(fields, f"{provider_id}.CONFIG_SCHEMA.fields"):
            logger.warning("[providers] %s.CONFIG_SCHEMA.fields 定义无效，已跳过", provider_id)
            continue
        schema["config_key"] = provider_id
        schema["provider"] = provider_id
        schema.setdefault("title", provider_id)
        schema.setdefault("description", "")
        try:
            schema["order"] = int(schema.get("order", 100))
        except (TypeError, ValueError):
            schema["order"] = 100
        schema["fields"] = fields
        schemas.append(schema)
    return sorted(
        schemas,
        key=lambda item: (item.get("order", 100), str(item.get("title", "")).casefold(), item["provider"].casefold()),
    )


def get_provider_config_schema(provider_id: str) -> dict[str, Any] | None:
    """返回指定 Provider 的 Schema 副本。"""
    for schema in get_provider_config_schemas():
        if schema.get("provider") == provider_id:
            return copy.deepcopy(schema)
    return None


def get_auth_providers() -> dict[str, ModuleType]:
    """返回声明认证生命周期或自定义认证入口的 Provider。"""
    return {
        provider_id: provider
        for provider_id, provider in get_providers().items()
        if hasattr(provider, "AUTH_DESCRIPTOR") or callable(getattr(provider, "get_auth_status", None))
    }


def get_providers_by_capability(capability: str) -> dict[str, ModuleType]:
    """返回声明某能力的 Provider。"""
    return {
        provider_id: provider
        for provider_id, provider in get_providers().items()
        if capability in getattr(provider, "CAPABILITIES", ())
    }


def _invoke(
    provider_id: str,
    provider: ModuleType | None,
    method: str,
    *args: Any,
    **kwargs: Any,
) -> ProviderCallOutcome[Any]:
    """内部类型化调用；公开 ``call_all``/``call_one`` 继续维持原返回形状。"""
    fn = getattr(provider, method, None) if provider is not None else None
    if not callable(fn):
        return ProviderCallOutcome(provider=provider_id, called=False)
    try:
        return ProviderCallOutcome(provider=provider_id, data=fn(*args, **kwargs))
    except Exception as exc:
        logger.warning("[providers] %s.%s() 调用失败: %s", provider_id, method, exc)
        return ProviderCallOutcome(provider=provider_id, error=str(exc))



def call_all(capability: str, method: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """调用所有拥有某能力的 Provider 方法，隔离单 Provider 异常。"""
    results: list[dict[str, Any]] = []
    for provider_id, provider in sorted(get_providers_by_capability(capability).items(), key=lambda item: item[0].casefold()):
        outcome = _invoke(provider_id, provider, method, *args, **kwargs)
        if outcome.called:
            results.append(outcome.to_call_all_payload())
    return results



def call_one(provider_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
    """调用一个 Provider 的公开方法；失败返回 ``None``。"""
    outcome = _invoke(provider_id, get_provider(provider_id), method, *args, **kwargs)
    return outcome.data if outcome.ok else None

    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("[providers] %s.%s() 调用失败: %s", provider_id, method, exc)
        return None
