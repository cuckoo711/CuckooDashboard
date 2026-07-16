"""Settings 字段校验与 Provider CONFIG_SCHEMA 解释。"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from contracts.provider import ProviderStatus, ProviderStatusPayload
from contracts.settings import ProviderPanel, SecretView
from providers import get_provider_config_schemas, get_providers
from providers.runtime_config import get_provider_config

SECRET_MASK = "••••••"
_MISSING = object()
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SettingsValidationError(ValueError):
    """配置后台输入不符合约束。"""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field

    def as_dict(self) -> dict[str, str]:
        result = {"message": str(self)}
        if self.field:
            result["field"] = self.field
        return result


def mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def secret_view(value: Any) -> SecretView:
    configured = isinstance(value, str) and bool(value)
    return {"configured": configured, "masked": SECRET_MASK if configured else ""}


def optional_string(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SettingsValidationError("必须是字符串或留空", field)
    value = value.strip()
    return value or None


def required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsValidationError("不能为空", field)
    return value.strip()


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsValidationError("必须是布尔值", field)
    return value


def finite_number(value: Any, field: str, *, minimum: float | None = None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsValidationError("必须是数字", field)
    number = float(value)
    if not math.isfinite(number):
        raise SettingsValidationError("必须是有限数字", field)
    if minimum is not None and number < minimum:
        raise SettingsValidationError(f"不能小于 {minimum:g}", field)
    return value if isinstance(value, int) else number


def integer(value: Any, field: str, *, minimum: int = 0) -> int:
    number = finite_number(value, field, minimum=minimum)
    if float(number) != int(number):
        raise SettingsValidationError("必须是整数", field)
    return int(number)


def http_url(value: Any, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise SettingsValidationError("必须是 URL 字符串", field)
    value = value.strip()
    if not value:
        if required:
            raise SettingsValidationError("不能为空", field)
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SettingsValidationError("必须是 http:// 或 https:// URL", field)
    return value.rstrip("/")


def schema_default(spec: Mapping[str, Any]) -> Any:
    if "default" in spec:
        return copy.deepcopy(spec["default"])
    field_type = spec.get("type")
    if field_type == "boolean":
        return False
    if field_type in {"string", "secret", "url", "time", "select", "color"}:
        return ""
    if field_type in {"string_list", "object_list"}:
        return []
    if field_type == "key_value_map":
        return {}
    return None


def field_by_key(fields: Any, key: str) -> Mapping[str, Any] | None:
    if not isinstance(fields, list):
        return None
    for field in fields:
        if isinstance(field, Mapping) and field.get("key") == key:
            return field
    return None


def public_value(value: Any, spec: Mapping[str, Any]) -> Any:
    field_type = spec.get("type")
    if value is _MISSING or value is None:
        value = schema_default(spec)
    if field_type == "secret":
        return secret_view(value)
    if field_type == "object_list":
        rows: list[dict[str, Any]] = []
        identity_key = spec.get("identity_key")
        raw_rows = value if isinstance(value, list) else []
        item_fields = spec.get("item_fields", [])
        for raw_row in raw_rows:
            if isinstance(raw_row, str) and identity_key == "url":
                raw_row = {"url": raw_row}
            if not isinstance(raw_row, Mapping):
                continue
            row: dict[str, Any] = {}
            if isinstance(identity_key, str) and identity_key in raw_row:
                row[f"__original_{identity_key}"] = raw_row.get(identity_key)
            for item_spec in item_fields if isinstance(item_fields, list) else []:
                if not isinstance(item_spec, Mapping) or not isinstance(item_spec.get("key"), str):
                    continue
                key = item_spec["key"]
                row[key] = public_value(raw_row.get(key, _MISSING), item_spec)
            rows.append(row)
        return rows
    if field_type == "key_value_map":
        return copy.deepcopy(value) if isinstance(value, Mapping) else {}
    if field_type == "string_list":
        return [str(item) for item in value] if isinstance(value, list) else []
    return copy.deepcopy(value)


def provider_values(provider_schema: Mapping[str, Any], raw_config: Any) -> dict[str, Any]:
    current = mapping(raw_config)
    result: dict[str, Any] = {}
    for spec in provider_schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            continue
        key = spec["key"]
        result[key] = public_value(current.get(key, _MISSING), spec)
    return result


def _provider_status(provider: Any) -> ProviderStatusPayload:
    get_status = getattr(provider, "get_status", None)
    if not callable(get_status):
        raw = {"status": "unknown", "ok": False, "enabled": True, "error": None}
    else:
        try:
            value = get_status()
            raw = dict(value) if isinstance(value, Mapping) else {"status": "unknown", "ok": False}
        except Exception as exc:
            raw = {"status": "error", "ok": False, "enabled": True, "error": str(exc)}
    return ProviderStatus.from_value(raw).to_provider_payload()



def provider_panels(config: Mapping[str, Any]) -> list[ProviderPanel]:

    """根据动态 Provider Schema 生成设置页 panel 与脱敏公开值。"""
    providers = get_providers()
    raw_provider_config = mapping(config.get("providers"))
    panels: list[ProviderPanel] = []
    for provider_schema in get_provider_config_schemas():
        provider_name = provider_schema["provider"]
        config_key = provider_schema["config_key"]
        provider = providers.get(provider_name)
        panel = {
            "provider": provider_name,
            "config_key": config_key,
            "title": provider_schema.get("title", provider_name),
            "description": provider_schema.get("description", ""),
            "order": provider_schema.get("order", 100),
            "fields": copy.deepcopy(provider_schema.get("fields", [])),
            "status_only_auth": bool(provider_schema.get("status_only_auth")),
            "values": provider_values(
                provider_schema,
                get_provider_config(provider_name, raw_provider_config.get(config_key, {})),
            ),
        }
        if provider is not None:
            panel["status"] = _provider_status(provider)
            auth_status = getattr(provider, "get_auth_status", None)
            if callable(auth_status):
                try:
                    panel["auth"] = dict(auth_status())
                except Exception:
                    panel["auth"] = {"status": "unknown", "authenticated": False}
            descriptor = getattr(provider, "AUTH_DESCRIPTOR", None)
            if isinstance(descriptor, Mapping):
                panel["auth_descriptor"] = copy.deepcopy(dict(descriptor))
        panels.append(panel)
    return panels


def _select_values(spec: Mapping[str, Any]) -> set[str]:
    options = spec.get("options", [])
    values: set[str] = set()
    if isinstance(options, list):
        for option in options:
            value = option.get("value") if isinstance(option, Mapping) else option
            if isinstance(value, str):
                values.add(value)
    return values


def validate_field(value: Any, spec: Mapping[str, Any], field: str) -> Any:
    field_type = spec.get("type")
    if value is _MISSING:
        return schema_default(spec)
    if field_type == "boolean":
        return boolean(value, field)
    if field_type == "string":
        if value is None or value == "":
            if spec.get("required"):
                raise SettingsValidationError("不能为空", field)
            return ""
        return required_string(value, field)
    if field_type == "url":
        return http_url(value, field, required=bool(spec.get("required")))
    if field_type == "integer":
        return integer(value, field, minimum=int(spec.get("min", 0)))
    if field_type == "number":
        return finite_number(value, field, minimum=spec.get("min"))
    if field_type == "select":
        result = optional_string(value, field) or ""
        allowed = _select_values(spec)
        if allowed and result not in allowed:
            raise SettingsValidationError("不是有效选项", field)
        return result
    if field_type == "color":
        result = required_string(value, field)
        if not _COLOR_RE.fullmatch(result):
            raise SettingsValidationError("颜色必须为 #RRGGBB", field)
        return result
    if field_type == "time":
        result = required_string(value, field)
        if not _TIME_RE.fullmatch(result):
            raise SettingsValidationError("时间必须为 HH:MM", field)
        return result
    if field_type == "string_list":
        if not isinstance(value, list):
            raise SettingsValidationError("必须是列表", field)
        return [required_string(item, f"{field}[{idx}]") for idx, item in enumerate(value)]
    if field_type == "key_value_map":
        if not isinstance(value, Mapping):
            raise SettingsValidationError("必须是对象", field)
        value_type = spec.get("value_type", "number")
        item_spec = {"type": value_type, "min": spec.get("min", 0)}
        return {
            required_string(key, f"{field}.key"): validate_field(amount, item_spec, f"{field}.{key}")
            for key, amount in value.items()
        }
    if field_type == "object_list":
        if not isinstance(value, list):
            raise SettingsValidationError("必须是列表", field)
        return value
    raise SettingsValidationError(f"不支持的字段类型: {field_type}", field)


def provider_schema_map() -> dict[str, tuple[str, Mapping[str, Any]]]:
    return {
        provider_schema["config_key"]: (provider_schema["provider"], provider_schema)
        for provider_schema in get_provider_config_schemas()
    }


def find_identity(row: Any, identity_key: str | None) -> str | None:
    if isinstance(row, str) and identity_key == "url":
        return row
    if isinstance(row, Mapping) and isinstance(identity_key, str):
        value = row.get(identity_key)
        return value.strip() if isinstance(value, str) else value
    return None


def _build_object_list(
    raw_value: Any,
    current_value: Any,
    spec: Mapping[str, Any],
    field: str,
    provider_secret_updates: Any,
    apply_secret_update: Callable[[Any, Any, str], str],
) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        raise SettingsValidationError("必须是列表", field)
    identity_key = spec.get("identity_key")
    if not isinstance(identity_key, str) or not identity_key:
        raise SettingsValidationError("object_list 缺少 identity_key", field)
    item_fields = spec.get("item_fields", [])
    if not isinstance(item_fields, list):
        raise SettingsValidationError("item_fields 必须是列表", field)

    old_rows: list[Any] = current_value if isinstance(current_value, list) else []
    old_by_identity = {
        identity: (row if isinstance(row, Mapping) else {identity_key: identity})
        for row in old_rows
        if (identity := find_identity(row, identity_key)) is not None
    }
    updates: dict[str, Mapping[str, Any]] = {}
    if provider_secret_updates is not None:
        if not isinstance(provider_secret_updates, list):
            raise SettingsValidationError("对象列表敏感更新必须是列表", f"secrets.{field}")
        for idx, update in enumerate(provider_secret_updates):
            if not isinstance(update, Mapping):
                raise SettingsValidationError("必须是对象", f"secrets.{field}[{idx}]")
            original = update.get("original_identity") or update.get("identity")
            if not isinstance(original, str) or not original.strip():
                raise SettingsValidationError("缺少列表项 identity", f"secrets.{field}[{idx}]")
            updates[original.strip()] = update

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw_row in enumerate(raw_value):
        if not isinstance(raw_row, Mapping):
            raise SettingsValidationError("列表项必须是对象", f"{field}[{idx}]")
        identity = raw_row.get(identity_key)
        if isinstance(identity, str):
            identity = identity.strip()
        if not identity:
            continue
        if not isinstance(identity, str):
            raise SettingsValidationError("identity 必须是字符串", f"{field}[{idx}].{identity_key}")
        item_field = f"{field}[{idx}]"
        identity = validate_field(
            identity,
            field_by_key(item_fields, identity_key) or {"type": "string"},
            f"{item_field}.{identity_key}",
        )
        if identity.casefold() in seen:
            raise SettingsValidationError("列表项不能重复", f"{item_field}.{identity_key}")
        seen.add(identity.casefold())
        original_identity = raw_row.get(f"__original_{identity_key}") or identity
        old_row = old_by_identity.get(original_identity) or old_by_identity.get(identity) or {}
        update = updates.get(original_identity) or updates.get(identity) or {}
        update_fields = update.get("fields", {}) if isinstance(update, Mapping) else {}
        if not isinstance(update_fields, Mapping):
            raise SettingsValidationError("fields 必须是对象", f"secrets.{field}")

        output: dict[str, Any] = {}
        for item_spec in item_fields:
            if not isinstance(item_spec, Mapping) or not isinstance(item_spec.get("key"), str):
                raise SettingsValidationError("item_fields 定义无效", field)
            key = item_spec["key"]
            current_item = old_row.get(key) if isinstance(old_row, Mapping) else None
            if item_spec.get("type") == "secret":
                output[key] = apply_secret_update(
                    update_fields.get(key), current_item, f"secrets.{field}[{idx}].{key}"
                )
            elif key == identity_key:
                output[key] = identity
            else:
                output[key] = validate_field(raw_row.get(key, _MISSING), item_spec, f"{item_field}.{key}")
        result.append(output)
    return result


def build_provider_config(
    provider_name: str,
    provider_schema: Mapping[str, Any],
    incoming: Any,
    current: Any,
    secrets: Mapping[str, Any],
    apply_secret_update: Callable[[Any, Any, str], str],
) -> dict[str, Any]:
    """按 Provider Schema 构建包含运行时 secret 的完整配置。"""
    config_key = provider_schema["config_key"]
    if not isinstance(incoming, Mapping):
        raise SettingsValidationError("Provider 配置必须是对象", f"providers.{config_key}")
    current_config = dict(current) if isinstance(current, Mapping) else {}
    result = copy.deepcopy(current_config)
    for spec in provider_schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            raise SettingsValidationError("Provider Schema 字段定义无效", f"providers.{config_key}")
        key = spec["key"]
        path = f"providers.{config_key}.{key}"
        field_type = spec.get("type")
        current_value = current_config.get(key, schema_default(spec))
        if field_type == "secret":
            result[key] = apply_secret_update(secrets.get(path), current_value, f"secrets.{path}")
        elif key in incoming:
            if field_type == "object_list":
                result[key] = _build_object_list(
                    incoming[key],
                    current_value,
                    spec,
                    path,
                    secrets.get(path),
                    apply_secret_update,
                )
            else:
                result[key] = validate_field(incoming[key], spec, path)
        elif key not in result:
            result[key] = schema_default(spec)

    provider = get_providers().get(provider_name)
    validator = getattr(provider, "validate_config", None) if provider is not None else None
    if callable(validator):
        try:
            validator(result)
        except SettingsValidationError:
            raise
        except Exception as exc:
            raise SettingsValidationError(str(exc), f"providers.{config_key}") from exc
    return result
