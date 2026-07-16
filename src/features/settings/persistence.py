"""Settings secret 操作、Vault 状态与敏感字段读取。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from core.config import load_config
from core.credentials import VaultError, get_global_secret, vault
from providers.runtime_config import get_provider_config

from . import schema


def global_vault_secret(key: str) -> str:
    try:
        value = get_global_secret(key, "")
    except VaultError:
        return ""
    return value if isinstance(value, str) else ""


def get_credential_revision() -> int | None:
    try:
        return vault.get_revision()
    except VaultError:
        return None


def secret_action(value: Any, field: str) -> tuple[str, str | None]:
    if value is None:
        return "keep", None
    if not isinstance(value, Mapping):
        raise schema.SettingsValidationError("敏感字段操作必须是对象", field)
    action = value.get("action", "keep")
    if action not in {"keep", "set", "clear"}:
        raise schema.SettingsValidationError("操作必须是 keep、set 或 clear", field)
    if action == "set":
        secret = value.get("value")
        if not isinstance(secret, str) or not secret:
            raise schema.SettingsValidationError("设置敏感字段时不能为空", f"{field}.value")
        return action, secret
    return action, None


def apply_secret_update(update: Any, current: Any, field: str) -> str:
    action, value = secret_action(update, field)
    if action == "keep":
        return current if isinstance(current, str) else ""
    if action == "clear":
        return ""
    return value or ""


def global_secret_update(secrets: Mapping[str, Any], path: str, current: Any) -> str:
    return apply_secret_update(secrets.get(path), current, f"secrets.{path}")


def extract_provider_secret_state(
    provider_schema: Mapping[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """从完整运行时 Provider 配置取出 Schema secret，并从 YAML 副本移除。"""
    result: dict[str, Any] = {"fields": {}, "objects": {}}
    for spec in provider_schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            continue
        key = spec["key"]
        if spec.get("type") == "secret":
            result["fields"][key] = str(config.pop(key, "") or "")
            continue
        if spec.get("type") != "object_list":
            continue
        identity_key = spec.get("identity_key")
        rows = config.get(key)
        if not isinstance(identity_key, str) or not isinstance(rows, list):
            continue
        item_specs = spec.get("item_fields", [])
        object_values: dict[str, dict[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = row.get(identity_key)
            if not isinstance(identity, str) or not identity:
                continue
            values: dict[str, str] = {}
            for item_spec in item_specs if isinstance(item_specs, list) else []:
                if not isinstance(item_spec, Mapping) or item_spec.get("type") != "secret":
                    continue
                item_key = item_spec.get("key")
                if isinstance(item_key, str):
                    values[item_key] = str(row.pop(item_key, "") or "")
            if values:
                object_values[identity] = values
        if object_values:
            result["objects"][key] = object_values
    return result


def persist_vault_changes(
    global_values: Mapping[str, str],
    provider_values: Mapping[str, dict[str, Any]],
    *,
    expected_revision: int | None = None,
) -> None:
    """在单次 Vault revision 事务中保存全局与 Provider secret 状态。"""
    if not global_values and not provider_values:
        return

    def apply(root: dict[str, Any]) -> None:
        global_state = root.setdefault("global", {})
        if not isinstance(global_state, dict):
            global_state = {}
            root["global"] = global_state
        for key, value in global_values.items():
            if value:
                global_state[key] = value
            else:
                global_state.pop(key, None)

        providers = root.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            root["providers"] = providers
        for provider_name, secret_state in provider_values.items():
            provider_state = providers.get(provider_name)
            if not isinstance(provider_state, dict):
                provider_state = {}
                providers[provider_name] = provider_state
            provider_state["config_secrets"] = copy.deepcopy(secret_state)
        return None

    vault.update(apply, expected_revision=expected_revision)


def reveal_secret(path: str, *, identity: str | None = None, field: str | None = None) -> str:
    """按 Schema 白名单读取单个敏感字段。"""
    config = load_config()
    if path == "dashboard.token":
        return global_vault_secret("dashboard_token")
    if path == "github_token":
        return global_vault_secret("github_token")
    if not isinstance(path, str) or not path.startswith("providers."):
        raise schema.SettingsValidationError("不允许查看该敏感字段", path)

    parts = path.split(".", 2)
    if len(parts) != 3:
        raise schema.SettingsValidationError("字段路径无效", path)
    _, config_key, field_key = parts
    schema_info = schema.provider_schema_map().get(config_key)
    if schema_info is None:
        raise schema.SettingsValidationError("Provider 未声明配置 Schema", path)
    provider_name, provider_schema = schema_info
    provider_config = get_provider_config(
        provider_name,
        schema.mapping(schema.mapping(config).get("providers")).get(config_key, {}),
    )
    field_spec = schema.field_by_key(provider_schema.get("fields"), field_key)
    if field_spec is None:
        raise schema.SettingsValidationError("字段不存在", path)
    if field_spec.get("type") == "secret":
        return str(schema.mapping(provider_config).get(field_key, "") or "")
    if field_spec.get("type") != "object_list":
        raise schema.SettingsValidationError("该字段不是敏感字段", path)
    if not identity or not field:
        raise schema.SettingsValidationError("缺少列表项 identity 或敏感字段名", path)
    item_spec = schema.field_by_key(field_spec.get("item_fields"), field)
    if item_spec is None or item_spec.get("type") != "secret":
        raise schema.SettingsValidationError("该列表字段不是敏感字段", f"{path}.{field}")
    identity_key = field_spec.get("identity_key")
    rows = provider_config.get(field_key, []) if isinstance(provider_config, Mapping) else []
    for row in rows if isinstance(rows, list) else []:
        if schema.find_identity(row, identity_key) == identity:
            return str(schema.mapping(row).get(field, "") or "")
    raise schema.SettingsValidationError("找不到对应列表项", path)
