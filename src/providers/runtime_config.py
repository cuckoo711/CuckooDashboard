"""Provider 运行时配置解析。

Provider Schema、默认值和 Vault secret 的合并属于 Provider 层，而不是 ``core``。
本模块是 Provider 与 Settings 服务读取 Provider 配置的唯一入口。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from core.config import load_config, save_config
from core.credentials import get_provider_state


def _schema(provider_id: str) -> dict[str, Any] | None:
    # 延迟导入避免 Registry 在发现插件时出现循环初始化。
    from providers import get_provider_config_schema

    value = get_provider_config_schema(provider_id)
    return dict(value) if isinstance(value, Mapping) else None


def _schema_defaults(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(schema, Mapping):
        return result
    fields = schema.get("fields")
    if not isinstance(fields, list):
        return result
    for field in fields:
        if not isinstance(field, Mapping) or not isinstance(field.get("key"), str):
            continue
        if "default" in field:
            result[field["key"]] = copy.deepcopy(field["default"])
    return result


def _merge_schema_secrets(provider_id: str, config: dict[str, Any], schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """将 Schema 声明的通用 secret 从 Provider Vault state 合并到副本。"""
    if not isinstance(schema, Mapping):
        return config
    state = get_provider_state(provider_id, {})
    if not isinstance(state, Mapping):
        return config
    secret_state = state.get("config_secrets")
    if not isinstance(secret_state, Mapping):
        return config

    fields = secret_state.get("fields") if isinstance(secret_state.get("fields"), Mapping) else {}
    objects = secret_state.get("objects") if isinstance(secret_state.get("objects"), Mapping) else {}
    merged = copy.deepcopy(config)
    for spec in schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            continue
        key = spec["key"]
        if spec.get("type") == "secret":
            if key in fields:
                merged[key] = copy.deepcopy(fields[key])
            continue
        if spec.get("type") != "object_list":
            continue
        identity_key = spec.get("identity_key")
        rows = merged.get(key)
        scoped = objects.get(key) if isinstance(objects.get(key), Mapping) else {}
        if not isinstance(identity_key, str) or not isinstance(rows, list):
            continue
        item_fields = spec.get("item_fields")
        if not isinstance(item_fields, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = row.get(identity_key)
            saved = scoped.get(identity) if isinstance(identity, str) else None
            if not isinstance(saved, Mapping):
                continue
            for item_spec in item_fields:
                if not isinstance(item_spec, Mapping) or item_spec.get("type") != "secret":
                    continue
                item_key = item_spec.get("key")
                if isinstance(item_key, str) and item_key in saved:
                    row[item_key] = copy.deepcopy(saved[item_key])
    return merged


def get_provider_config(provider_id: str, default: Any = None) -> Any:
    """读取一个 Provider 的运行时配置、Schema 默认值和 Vault secret。"""
    providers = load_config().get("providers", {})
    raw_value = providers.get(provider_id) if isinstance(providers, Mapping) else None
    if raw_value is None:
        raw_value = default
    if raw_value is None:
        raw_value = {}
    if not isinstance(raw_value, Mapping):
        return copy.deepcopy(raw_value)

    schema = _schema(provider_id)
    resolved = _schema_defaults(schema)
    resolved.update(copy.deepcopy(dict(raw_value)))
    return _merge_schema_secrets(provider_id, resolved, schema)


def get_raw_provider_config(provider_id: str, default: Any = None) -> Any:
    """读取 YAML 中的 Provider 非敏感配置，不应用 Schema/Vault。"""
    providers = load_config().get("providers", {})
    if not isinstance(providers, Mapping):
        return copy.deepcopy(default)
    value = providers.get(provider_id, default)
    return copy.deepcopy(value)


def set_provider_config(provider_id: str, value: Any) -> None:
    """保存一个 Provider 的非敏感 YAML 配置。"""
    config = copy.deepcopy(load_config())
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    providers[provider_id] = copy.deepcopy(value)
    save_config(config)
