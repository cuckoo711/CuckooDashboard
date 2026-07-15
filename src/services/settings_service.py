"""配置后台的读取、脱敏、Schema 校验与保存逻辑。"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from core.config import CONFIG_VERSION, load_config, migrate_config, save_config
from providers import get_provider_config_schemas, get_providers
from services.font_service import font_exists, list_fonts
from services.spectrum_service import list_capture_devices, load_music_offsets, request_capture_restart, save_music_offsets
from services.theme import THEMES

SECRET_MASK = "••••••"
_MISSING = object()
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LOG_MODES = {"daily", "size"}


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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _secret_view(value: Any) -> dict[str, Any]:
    configured = isinstance(value, str) and bool(value)
    return {"configured": configured, "masked": SECRET_MASK if configured else ""}


def _optional_string(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SettingsValidationError("必须是字符串或留空", field)
    value = value.strip()
    return value or None


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsValidationError("不能为空", field)
    return value.strip()


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsValidationError("必须是布尔值", field)
    return value


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsValidationError("必须是数字", field)
    number = float(value)
    if not math.isfinite(number):
        raise SettingsValidationError("必须是有限数字", field)
    if minimum is not None and number < minimum:
        raise SettingsValidationError(f"不能小于 {minimum:g}", field)
    return value if isinstance(value, int) else number


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    number = _finite_number(value, field, minimum=minimum)
    if float(number) != int(number):
        raise SettingsValidationError("必须是整数", field)
    return int(number)


def _http_url(value: Any, field: str, *, required: bool = False) -> str:
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


def _time_range(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", field)
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, str) or not _TIME_RE.fullmatch(start):
        raise SettingsValidationError("开始时间必须为 HH:MM", f"{field}.start")
    if not isinstance(end, str) or not _TIME_RE.fullmatch(end):
        raise SettingsValidationError("结束时间必须为 HH:MM", f"{field}.end")
    if start == end:
        raise SettingsValidationError("起止时间不能相同", field)
    return {"start": start, "end": end}


def _validate_off_peak(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.off_peak_badge")
    enabled = _boolean(value.get("enabled", True), "dashboard.off_peak_badge.enabled")
    ranges = value.get("ranges", [])
    if not isinstance(ranges, list):
        raise SettingsValidationError("必须是列表", "dashboard.off_peak_badge.ranges")
    return {
        "enabled": enabled,
        "ranges": [_time_range(item, f"dashboard.off_peak_badge.ranges[{idx}]") for idx, item in enumerate(ranges)],
    }


def _validate_vibe(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.vibe_coding")
    raw_ring = value.get("ring", {})
    raw_model = value.get("model_bars", {})
    if not isinstance(raw_ring, Mapping) or not isinstance(raw_model, Mapping):
        raise SettingsValidationError("数据源选择必须是对象", "dashboard.vibe_coding")

    ring = {
        "provider": _optional_string(raw_ring.get("provider"), "dashboard.vibe_coding.ring.provider"),
        "item": _optional_string(raw_ring.get("item"), "dashboard.vibe_coding.ring.item"),
    }
    model_bars = {
        "provider": _optional_string(raw_model.get("provider"), "dashboard.vibe_coding.model_bars.provider"),
    }

    raw_balances = value.get("balances", [])
    if not isinstance(raw_balances, list):
        raise SettingsValidationError("必须是列表", "dashboard.vibe_coding.balances")
    balances: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_balances):
        field = f"dashboard.vibe_coding.balances[{idx}]"
        if not isinstance(raw, Mapping):
            raise SettingsValidationError("必须是对象", field)
        enabled = _boolean(raw.get("enabled", True), f"{field}.enabled")
        provider_raw = raw.get("provider")
        if not enabled and not provider_raw:
            continue
        provider = _required_string(provider_raw, f"{field}.provider")
        provider_key = provider.casefold()
        if provider_key in seen:
            raise SettingsValidationError("余额 Provider 不能重复", f"{field}.provider")
        seen.add(provider_key)
        name = _optional_string(raw.get("name"), f"{field}.name") or provider
        color = raw.get("color") or "#888888"
        if not isinstance(color, str) or not _COLOR_RE.fullmatch(color):
            raise SettingsValidationError("颜色必须为 #RRGGBB", f"{field}.color")
        balances.append({"provider": provider, "name": name, "color": color, "enabled": enabled})
    return {"ring": ring, "model_bars": model_bars, "balances": balances}


def _validate_font(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.font")
    enabled = _boolean(value.get("enabled", False), "dashboard.font.enabled")
    raw_filename = value.get("filename")
    filename = _optional_string(raw_filename, "dashboard.font.filename")
    if filename and not font_exists(filename):
        raise SettingsValidationError("字体文件不存在，请先上传", "dashboard.font.filename")
    return {"enabled": enabled, "filename": filename or ""}


# font_size 各分类的最小/最大合法 px 值
_FONT_SIZE_KEYS: dict[str, tuple[int, int, int]] = {
    # (field_key, min_px, max_px, default_px)
    "title":     (4, 80, 16),
    "clock":     (4, 80, 22),
    "date":      (4, 80, 15),
    "card_head": (4, 80, 10),
    "card_foot": (4, 80, 10),
    "card_body": (4, 80, 10),
}
_FONT_OFFSET_RANGE = (-20, 20)


def _validate_font_size(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.font_size")
    result: dict[str, Any] = {}
    title_text = _optional_string(value.get("title_text"), "dashboard.font_size.title_text")
    result["title_text"] = title_text or "Cuckoo Dashboard"
    for key, (lo, hi, default) in _FONT_SIZE_KEYS.items():
        raw = value.get(key, default)
        field = f"dashboard.font_size.{key}"
        num = _integer(raw, field, minimum=lo)
        if num > hi:
            raise SettingsValidationError(f"不能超过 {hi}px", field)
        result[key] = num
    raw_offset = value.get("offset", 0)
    field = "dashboard.font_size.offset"
    num = _integer(raw_offset, field, minimum=_FONT_OFFSET_RANGE[0])
    if num > _FONT_OFFSET_RANGE[1]:
        raise SettingsValidationError(f"不能超过 {_FONT_OFFSET_RANGE[1]}", field)
    result["offset"] = num
    return result


def _validate_hardware(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides")
    result: dict[str, Any] = {}
    for key in ("cpu_model", "mem_name", "gpu_model"):
        raw = value.get(key)
        result[key] = None if raw is None or raw == "" else _required_string(raw, f"hardware_overrides.{key}")

    mem = value.get("mem_installed_gb")
    result["mem_installed_gb"] = None if mem is None or mem == "" else _finite_number(
        mem, "hardware_overrides.mem_installed_gb", minimum=0.01
    )

    raw_vram = value.get("gpu_vram_gb", {})
    if not isinstance(raw_vram, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides.gpu_vram_gb")
    result["gpu_vram_gb"] = {
        _required_string(name, "hardware_overrides.gpu_vram_gb"): _finite_number(
            amount, f"hardware_overrides.gpu_vram_gb.{name}", minimum=0.01
        )
        for name, amount in raw_vram.items()
    }

    raw_apu = value.get("apu_device_ids")
    if raw_apu is None or raw_apu == "":
        result["apu_device_ids"] = None
    else:
        if not isinstance(raw_apu, list):
            raise SettingsValidationError("必须是列表", "hardware_overrides.apu_device_ids")
        result["apu_device_ids"] = [
            _required_string(item, f"hardware_overrides.apu_device_ids[{idx}]")
            for idx, item in enumerate(raw_apu)
        ]
    return result


def _validate_logging(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "logging")
    level = _required_string(value.get("level", "INFO"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        raise SettingsValidationError("级别必须为 DEBUG、INFO、WARNING 或 ERROR", "logging.level")
    mode = _required_string(value.get("mode", "daily"), "logging.mode").lower()
    if mode not in _LOG_MODES:
        raise SettingsValidationError("模式必须为 daily 或 size", "logging.mode")
    return {
        "level": level,
        "mode": mode,
        "dir": _required_string(value.get("dir", "logs"), "logging.dir"),
        "keep_days": _integer(value.get("keep_days", 7), "logging.keep_days", minimum=0),
        "max_size_mb": _integer(value.get("max_size_mb", 5), "logging.max_size_mb", minimum=1),
        "max_backups": _integer(value.get("max_backups", 5), "logging.max_backups", minimum=0),
        "console": _boolean(value.get("console", True), "logging.console"),
    }


def _schema_default(spec: Mapping[str, Any]) -> Any:
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


def _field_by_key(fields: Any, key: str) -> Mapping[str, Any] | None:
    if not isinstance(fields, list):
        return None
    for field in fields:
        if isinstance(field, Mapping) and field.get("key") == key:
            return field
    return None


def _public_value(value: Any, spec: Mapping[str, Any]) -> Any:
    field_type = spec.get("type")
    if value is _MISSING or value is None:
        value = _schema_default(spec)
    if field_type == "secret":
        return _secret_view(value)
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
                row[key] = _public_value(raw_row.get(key, _MISSING), item_spec)
            rows.append(row)
        return rows
    if field_type == "key_value_map":
        return copy.deepcopy(value) if isinstance(value, Mapping) else {}
    if field_type == "string_list":
        return [str(item) for item in value] if isinstance(value, list) else []
    return copy.deepcopy(value)


def _schema_provider_values(schema: Mapping[str, Any], raw_config: Any) -> dict[str, Any]:
    current = _mapping(raw_config)
    result: dict[str, Any] = {}
    for spec in schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            continue
        key = spec["key"]
        result[key] = _public_value(current.get(key, _MISSING), spec)
    return result


def _provider_status(provider: Any) -> dict[str, Any]:
    get_status = getattr(provider, "get_status", None)
    if not callable(get_status):
        return {"status": "unknown", "ok": False, "enabled": True, "error": None}
    try:
        value = get_status()
        return dict(value) if isinstance(value, Mapping) else {"status": "unknown", "ok": False}
    except Exception as exc:
        return {"status": "error", "ok": False, "enabled": True, "error": str(exc)}


def _provider_panels(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    providers = get_providers()
    raw_provider_config = _mapping(config.get("providers"))
    panels: list[dict[str, Any]] = []
    for schema in get_provider_config_schemas():
        provider_name = schema["provider"]
        config_key = schema["config_key"]
        provider = providers.get(provider_name)
        panel = {
            "provider": provider_name,
            "config_key": config_key,
            "title": schema.get("title", provider_name),
            "description": schema.get("description", ""),
            "order": schema.get("order", 100),
            "fields": copy.deepcopy(schema.get("fields", [])),
            "status_only_auth": bool(schema.get("status_only_auth")),
            "values": _schema_provider_values(schema, raw_provider_config.get(config_key, {})),
        }
        if provider is not None:
            panel["status"] = _provider_status(provider)
        panels.append(panel)
    return panels


def get_settings_options() -> dict[str, Any]:
    """返回不触发外部网络请求的跨 Provider 选项。"""
    ring: list[str] = []
    model_bars: list[str] = []
    balances: list[str] = []
    for name, provider in sorted(get_providers().items(), key=lambda item: item[0].casefold()):
        capabilities = getattr(provider, "CAPABILITIES", ())
        if "token_plan" in capabilities and callable(getattr(provider, "get_plan_usage", None)):
            ring.append(name)
        if callable(getattr(provider, "get_model_breakdown", None)) or callable(
            getattr(provider, "get_channel_breakdown", None)
        ):
            model_bars.append(name)
        if "balance" in capabilities and callable(getattr(provider, "get_balance", None)):
            balances.append(name)
    return {
        "ring_providers": ring,
        "model_bar_providers": model_bars,
        "balance_providers": balances,
        "themes": [item["name"] for item in THEMES],
        "fonts": list_fonts(),
        "capture_devices": list_capture_devices(),
    }


def _public_global_config(config: Mapping[str, Any]) -> dict[str, Any]:
    active = _mapping(config)
    dashboard = _mapping(active.get("dashboard"))
    off_peak = _mapping(dashboard.get("off_peak_badge"))
    vibe = _mapping(dashboard.get("vibe_coding"))
    ring = _mapping(vibe.get("ring"))
    model_bars = _mapping(vibe.get("model_bars"))
    raw_balances = vibe.get("balances", [])
    balances = [
        {
            "provider": item.get("provider", ""),
            "name": item.get("name", ""),
            "color": item.get("color", "#888888"),
            "enabled": item.get("enabled", True),
        }
        for item in raw_balances
        if isinstance(item, Mapping)
    ] if isinstance(raw_balances, list) else []
    font = _mapping(dashboard.get("font"))
    font_size = _mapping(dashboard.get("font_size"))
    hardware = _mapping(active.get("hardware_overrides"))
    logging_config = _mapping(active.get("logging"))
    return {
        "config_version": active.get("config_version", CONFIG_VERSION),
        "dashboard": {
            "token": _secret_view(dashboard.get("token")),
            "off_peak_badge": {
                "enabled": off_peak.get("enabled", True),
                "ranges": copy.deepcopy(off_peak.get("ranges", [{"start": "00:00", "end": "08:00"}])),
            },
            "vibe_coding": {
                "ring": {"provider": ring.get("provider") or "", "item": ring.get("item") or ""},
                "model_bars": {"provider": model_bars.get("provider") or ""},
                "balances": balances,
            },
            "font": {
                "enabled": bool(font.get("enabled", False)),
                "filename": str(font.get("filename") or ""),
            },
            "font_size": {
                "title_text": str(font_size.get("title_text") or "Cuckoo Dashboard"),
                "title": int(font_size.get("title", 16)),
                "clock": int(font_size.get("clock", 22)),
                "date": int(font_size.get("date", 15)),
                "card_head": int(font_size.get("card_head", 10)),
                "card_foot": int(font_size.get("card_foot", 10)),
                "card_body": int(font_size.get("card_body", 10)),
                "offset": int(font_size.get("offset", 0)),
            },
        },
        "github_token": _secret_view(active.get("github_token")),
        "hardware_overrides": {
            "cpu_model": hardware.get("cpu_model"),
            "mem_installed_gb": hardware.get("mem_installed_gb"),
            "mem_name": hardware.get("mem_name"),
            "gpu_model": hardware.get("gpu_model"),
            "gpu_vram_gb": copy.deepcopy(hardware.get("gpu_vram_gb", {})),
            "apu_device_ids": copy.deepcopy(hardware.get("apu_device_ids")),
        },
        "logging": {
            "level": logging_config.get("level", "INFO"),
            "mode": logging_config.get("mode", "daily"),
            "dir": logging_config.get("dir", "logs"),
            "keep_days": logging_config.get("keep_days", 7),
            "max_size_mb": logging_config.get("max_size_mb", 5),
            "max_backups": logging_config.get("max_backups", 5),
            "console": logging_config.get("console", True),
        },
        "theme": active.get("theme", "dark"),
        "lyric_offset": active.get("lyric_offset", 0.0),
        "vibe_active": bool(active.get("vibe_active", False)),
        "music": load_music_offsets(),
    }


def get_settings_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "config": _public_global_config(config),
        "providers": _provider_panels(config),
        "options": get_settings_options(),
    }


def _secret_action(value: Any, field: str) -> tuple[str, str | None]:
    if value is None:
        return "keep", None
    if not isinstance(value, Mapping):
        raise SettingsValidationError("敏感字段操作必须是对象", field)
    action = value.get("action", "keep")
    if action not in {"keep", "set", "clear"}:
        raise SettingsValidationError("操作必须是 keep、set 或 clear", field)
    if action == "set":
        secret = value.get("value")
        if not isinstance(secret, str) or not secret:
            raise SettingsValidationError("设置敏感字段时不能为空", f"{field}.value")
        return action, secret
    return action, None


def _apply_secret_update(update: Any, current: Any, field: str) -> str:
    action, value = _secret_action(update, field)
    if action == "keep":
        return current if isinstance(current, str) else ""
    if action == "clear":
        return ""
    return value or ""


def _select_values(spec: Mapping[str, Any]) -> set[str]:
    options = spec.get("options", [])
    values: set[str] = set()
    if isinstance(options, list):
        for option in options:
            if isinstance(option, Mapping):
                value = option.get("value")
            else:
                value = option
            if isinstance(value, str):
                values.add(value)
    return values


def _validate_field(value: Any, spec: Mapping[str, Any], field: str) -> Any:
    field_type = spec.get("type")
    if value is _MISSING:
        return _schema_default(spec)
    if field_type == "boolean":
        return _boolean(value, field)
    if field_type == "string":
        if value is None or value == "":
            if spec.get("required"):
                raise SettingsValidationError("不能为空", field)
            return ""
        return _required_string(value, field)
    if field_type == "url":
        return _http_url(value, field, required=bool(spec.get("required")))
    if field_type == "integer":
        return _integer(value, field, minimum=int(spec.get("min", 0)))
    if field_type == "number":
        return _finite_number(value, field, minimum=spec.get("min"))
    if field_type == "select":
        result = _optional_string(value, field) or ""
        allowed = _select_values(spec)
        if allowed and result not in allowed:
            raise SettingsValidationError("不是有效选项", field)
        return result
    if field_type == "color":
        result = _required_string(value, field)
        if not _COLOR_RE.fullmatch(result):
            raise SettingsValidationError("颜色必须为 #RRGGBB", field)
        return result
    if field_type == "time":
        result = _required_string(value, field)
        if not _TIME_RE.fullmatch(result):
            raise SettingsValidationError("时间必须为 HH:MM", field)
        return result
    if field_type == "string_list":
        if not isinstance(value, list):
            raise SettingsValidationError("必须是列表", field)
        return [_required_string(item, f"{field}[{idx}]") for idx, item in enumerate(value)]
    if field_type == "key_value_map":
        if not isinstance(value, Mapping):
            raise SettingsValidationError("必须是对象", field)
        value_type = spec.get("value_type", "number")
        item_spec = {"type": value_type, "min": spec.get("min", 0)}
        return {
            _required_string(key, f"{field}.key"): _validate_field(amount, item_spec, f"{field}.{key}")
            for key, amount in value.items()
        }
    if field_type == "object_list":
        if not isinstance(value, list):
            raise SettingsValidationError("必须是列表", field)
        return value
    raise SettingsValidationError(f"不支持的字段类型: {field_type}", field)


def _provider_schema_map() -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for schema in get_provider_config_schemas():
        result[schema["config_key"]] = (schema["provider"], schema)
    return result


def _find_identity(row: Any, identity_key: str | None) -> str | None:
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
        if (identity := _find_identity(row, identity_key)) is not None
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
        identity = _validate_field(identity, _field_by_key(item_fields, identity_key) or {"type": "string"}, f"{item_field}.{identity_key}")
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
                output[key] = _apply_secret_update(update_fields.get(key), current_item, f"secrets.{field}[{idx}].{key}")
            elif key == identity_key:
                output[key] = identity
            else:
                output[key] = _validate_field(raw_row.get(key, _MISSING), item_spec, f"{item_field}.{key}")
        result.append(output)
    return result


def _build_provider_config(
    provider_name: str,
    schema: Mapping[str, Any],
    incoming: Any,
    current: Any,
    secrets: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(incoming, Mapping):
        raise SettingsValidationError("Provider 配置必须是对象", f"providers.{schema['config_key']}")
    current_config = dict(current) if isinstance(current, Mapping) else {}
    result = copy.deepcopy(current_config)
    config_key = schema["config_key"]
    for spec in schema.get("fields", []):
        if not isinstance(spec, Mapping) or not isinstance(spec.get("key"), str):
            raise SettingsValidationError("Provider Schema 字段定义无效", f"providers.{config_key}")
        key = spec["key"]
        path = f"providers.{config_key}.{key}"
        field_type = spec.get("type")
        current_value = current_config.get(key, _schema_default(spec))
        if field_type == "secret":
            result[key] = _apply_secret_update(secrets.get(path), current_value, f"secrets.{path}")
        elif key in incoming:
            if field_type == "object_list":
                result[key] = _build_object_list(
                    incoming[key], current_value, spec, path, secrets.get(path)
                )
            else:
                result[key] = _validate_field(incoming[key], spec, path)
        elif key not in result:
            result[key] = _schema_default(spec)

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


def _global_secret_update(secrets: Mapping[str, Any], path: str, current: Any) -> str:
    return _apply_secret_update(secrets.get(path), current, f"secrets.{path}")


def _validate_music(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "music")
    device = value.get("capture_device", "auto")
    if device is None or device == "":
        device = "auto"
    if not isinstance(device, str):
        raise SettingsValidationError("采集设备必须是字符串", "music.capture_device")
    device = device.strip() or "auto"
    allowed = {"auto"} | {str(item.get("id") or "") for item in list_capture_devices()}
    # Allow stale ids to be saved only if they still look well-formed, but prefer known list.
    if device not in allowed and not (device.startswith("sc:") or device.startswith("sd:")):
        raise SettingsValidationError("未知采集设备", "music.capture_device")
    bins = _integer(value.get("bins", 48), "music.bins", minimum=16)
    if bins > 96:
        raise SettingsValidationError("bins 不能超过 96", "music.bins")
    render_fps = _integer(value.get("render_fps", 0), "music.render_fps", minimum=0)
    if render_fps and not 12 <= render_fps <= 60:
        raise SettingsValidationError("render_fps 必须为 0（自动）或 12-60", "music.render_fps")
    render_bars = _integer(value.get("render_bars", 0), "music.render_bars", minimum=0)
    if render_bars and not 12 <= render_bars <= 96:
        raise SettingsValidationError("render_bars 必须为 0（自动）或 12-96", "music.render_bars")
    return {
        "spectrum_enabled": _boolean(value.get("spectrum_enabled", True), "music.spectrum_enabled"),
        "auto_calibrate": _boolean(value.get("auto_calibrate", True), "music.auto_calibrate"),
        "spectrum_offset_ms": int(_finite_number(value.get("spectrum_offset_ms", 40), "music.spectrum_offset_ms")),
        "beat_lead_ms": int(_finite_number(value.get("beat_lead_ms", 20), "music.beat_lead_ms")),
        "bins": bins,
        "render_fps": render_fps,
        "render_bars": render_bars,
        "capture_device": device,
    }


def save_settings_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验并保存全局配置与 Provider 配置。"""
    if not isinstance(payload, Mapping):
        raise SettingsValidationError("请求体必须是对象")
    incoming = payload.get("config", {})
    secrets = payload.get("secrets", {})
    if not isinstance(incoming, Mapping):
        raise SettingsValidationError("config 必须是对象")
    if not isinstance(secrets, Mapping):
        raise SettingsValidationError("secrets 必须是对象")

    current, _ = migrate_config(load_config())
    next_config = copy.deepcopy(current)
    dashboard_input = incoming.get("dashboard")
    if dashboard_input is not None:
        if not isinstance(dashboard_input, Mapping):
            raise SettingsValidationError("必须是对象", "dashboard")
        dashboard = next_config.setdefault("dashboard", {})
        if not isinstance(dashboard, dict):
            dashboard = {}
            next_config["dashboard"] = dashboard
        if "off_peak_badge" in dashboard_input:
            dashboard["off_peak_badge"] = _validate_off_peak(dashboard_input["off_peak_badge"])
        if "vibe_coding" in dashboard_input:
            dashboard["vibe_coding"] = _validate_vibe(dashboard_input["vibe_coding"])
        if "font" in dashboard_input:
            dashboard["font"] = _validate_font(dashboard_input["font"])
        if "font_size" in dashboard_input:
            dashboard["font_size"] = _validate_font_size(dashboard_input["font_size"])
        dashboard["token"] = _global_secret_update(
            secrets, "dashboard.token", _mapping(current.get("dashboard")).get("token", "")
        )

    if "github_token" in secrets:
        next_config["github_token"] = _global_secret_update(secrets, "github_token", current.get("github_token", ""))

    if "hardware_overrides" in incoming:
        next_config["hardware_overrides"] = _validate_hardware(incoming["hardware_overrides"])
    if "logging" in incoming:
        next_config["logging"] = _validate_logging(incoming["logging"])

    for key in ("theme", "lyric_offset", "vibe_active"):
        if key not in incoming:
            continue
        if key == "theme":
            theme = _required_string(incoming[key], "theme")
            if theme not in {item["name"] for item in THEMES}:
                raise SettingsValidationError("主题不存在", "theme")
            next_config[key] = theme
        elif key == "lyric_offset":
            next_config[key] = _finite_number(incoming[key], "lyric_offset")
        else:
            next_config[key] = _boolean(incoming[key], "vibe_active")

    music_changed = False
    if "music" in incoming:
        validated_music = _validate_music(incoming["music"])
        old_music = load_music_offsets()
        # Persist via spectrum helper so device switch restarts capture cleanly.
        save_music_offsets(validated_music)
        next_config["music"] = dict(load_config().get("music") or validated_music)
        music_changed = (
            old_music.get("capture_device") != validated_music.get("capture_device")
            or old_music.get("spectrum_enabled") != validated_music.get("spectrum_enabled")
        )
        if music_changed:
            request_capture_restart("settings save")

    incoming_providers = incoming.get("providers", {})
    if not isinstance(incoming_providers, Mapping):
        raise SettingsValidationError("必须是对象", "providers")
    schema_map = _provider_schema_map()
    providers_config = next_config.get("providers")
    if not isinstance(providers_config, dict):
        providers_config = {}
        next_config["providers"] = providers_config
    for config_key, raw_provider in incoming_providers.items():
        if config_key not in schema_map:
            raise SettingsValidationError("未注册或未声明配置 Schema 的 Provider", f"providers.{config_key}")
        provider_name, schema = schema_map[config_key]
        providers_config[config_key] = _build_provider_config(
            provider_name,
            schema,
            raw_provider,
            providers_config.get(config_key, {}),
            secrets,
        )

    next_config["config_version"] = CONFIG_VERSION
    save_config(next_config)
    applied, errors = apply_runtime_config()
    return {
        "ok": True,
        "applied": applied,
        "errors": errors,
        **get_settings_payload(),
    }


def _schema_for_config_key(config_key: str) -> tuple[str, Mapping[str, Any]] | None:
    return _provider_schema_map().get(config_key)


def reveal_secret(path: str, *, identity: str | None = None, field: str | None = None) -> str:
    """按 Schema 白名单读取单个敏感字段。"""
    config = load_config()
    if path == "dashboard.token":
        return str(_mapping(config.get("dashboard")).get("token", "") or "")
    if path == "github_token":
        return str(config.get("github_token", "") or "")
    if not isinstance(path, str) or not path.startswith("providers."):
        raise SettingsValidationError("不允许查看该敏感字段", path)

    parts = path.split(".", 2)
    if len(parts) != 3:
        raise SettingsValidationError("字段路径无效", path)
    _, config_key, field_key = parts
    schema_info = _schema_for_config_key(config_key)
    if schema_info is None:
        raise SettingsValidationError("Provider 未声明配置 Schema", path)
    _, schema = schema_info
    provider_config = _mapping(_mapping(config).get("providers")).get(config_key, {})
    field_spec = _field_by_key(schema.get("fields"), field_key)
    if field_spec is None:
        raise SettingsValidationError("字段不存在", path)
    if field_spec.get("type") == "secret":
        return str(_mapping(provider_config).get(field_key, "") or "")
    if field_spec.get("type") != "object_list":
        raise SettingsValidationError("该字段不是敏感字段", path)
    if not identity or not field:
        raise SettingsValidationError("缺少列表项 identity 或敏感字段名", path)
    item_spec = _field_by_key(field_spec.get("item_fields"), field)
    if item_spec is None or item_spec.get("type") != "secret":
        raise SettingsValidationError("该列表字段不是敏感字段", f"{path}.{field}")
    identity_key = field_spec.get("identity_key")
    rows = provider_config.get(field_key, []) if isinstance(provider_config, Mapping) else []
    for row in rows if isinstance(rows, list) else []:
        if _find_identity(row, identity_key) == identity:
            return str(_mapping(row).get(field, "") or "")
    raise SettingsValidationError("找不到对应列表项", path)


def apply_runtime_config() -> tuple[list[str], list[str]]:
    """清理聚合缓存并动态调用所有 Provider 的 reload_config。"""
    applied: list[str] = []
    errors: list[str] = []
    try:
        from providers import invalidate_data_cache
        invalidate_data_cache()
        applied.append("providers")
    except Exception as exc:
        errors.append(f"providers: {exc}")

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
