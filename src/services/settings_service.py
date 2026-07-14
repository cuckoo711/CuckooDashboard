"""配置后台的读取、脱敏、校验与保存逻辑。"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from core.config import load_config, save_config
from services.theme import THEMES

SECRET_MASK = "••••••"
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LOG_MODES = {"daily", "size"}
_SECRET_KEYS = {
    "dashboard.token",
    "github_token",
    "local_platforms.password",
    "nug.password",
}


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
    if isinstance(value, int):
        return value
    return number


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
            # 允许用户先添加空行再关闭，保存时直接忽略该行。
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


def _entry_url(entry: Any) -> str:
    if isinstance(entry, Mapping):
        value = entry.get("url", "")
    else:
        value = entry
    return value.strip() if isinstance(value, str) else ""


def _entry_password(entry: Any) -> str | None:
    if isinstance(entry, Mapping):
        password = entry.get("password")
        return password if isinstance(password, str) and password else None
    return None


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


def _validate_local_urls(
    value: Any,
    current_urls: Any,
    url_secret_updates: Any,
) -> list[str | dict[str, str]]:
    if not isinstance(value, list):
        raise SettingsValidationError("必须是列表", "local_platforms.urls")
    if url_secret_updates is None:
        url_secret_updates = []
    if not isinstance(url_secret_updates, list):
        raise SettingsValidationError("必须是列表", "secrets.local_platforms.url_passwords")

    updates: dict[str, Mapping[str, Any]] = {}
    for idx, item in enumerate(url_secret_updates):
        if not isinstance(item, Mapping):
            raise SettingsValidationError("必须是对象", f"secrets.local_platforms.url_passwords[{idx}]")
        original = item.get("original_url") or item.get("url")
        if not isinstance(original, str) or not original.strip():
            raise SettingsValidationError("缺少原始 URL", f"secrets.local_platforms.url_passwords[{idx}]")
        updates[original.strip()] = item

    old_by_url = {_entry_url(item): item for item in (current_urls if isinstance(current_urls, list) else []) if _entry_url(item)}
    result: list[str | dict[str, str]] = []
    seen_urls: set[str] = set()
    for idx, raw in enumerate(value):
        if isinstance(raw, Mapping):
            url = raw.get("url", "")
            original_url = raw.get("original_url") or url
        else:
            url = raw
            original_url = raw
        if not isinstance(url, str):
            raise SettingsValidationError("必须是字符串或对象", f"local_platforms.urls[{idx}]")
        url = url.strip()
        original_url = original_url.strip() if isinstance(original_url, str) else ""
        if not url:
            continue
        url = _http_url(url, f"local_platforms.urls[{idx}].url", required=True)
        if url.casefold() in seen_urls:
            raise SettingsValidationError("URL 不能重复", f"local_platforms.urls[{idx}].url")
        seen_urls.add(url.casefold())

        old_entry = old_by_url.get(original_url) or old_by_url.get(url)
        old_password = _entry_password(old_entry)
        update = updates.get(original_url) or updates.get(url)
        action, secret = _secret_action(update, f"secrets.local_platforms.url_passwords[{idx}]")
        if action == "keep":
            secret = old_password
        if action == "set" and secret:
            result.append({"url": url, "password": secret})
        elif action == "clear":
            result.append(url)
        elif secret:
            result.append({"url": url, "password": secret})
        else:
            result.append(url)
    return result


def _validate_hardware(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides")
    result: dict[str, Any] = {}
    for key in ("cpu_model", "mem_name", "gpu_model"):
        raw = value.get(key)
        if raw is None or raw == "":
            result[key] = None
        else:
            result[key] = _required_string(raw, f"hardware_overrides.{key}")

    mem = value.get("mem_installed_gb")
    result["mem_installed_gb"] = None if mem is None or mem == "" else _finite_number(
        mem, "hardware_overrides.mem_installed_gb", minimum=0.01
    )

    raw_vram = value.get("gpu_vram_gb", {})
    if not isinstance(raw_vram, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides.gpu_vram_gb")
    vram: dict[str, float | int] = {}
    for name, amount in raw_vram.items():
        key = _required_string(name, "hardware_overrides.gpu_vram_gb")
        vram[key] = _finite_number(amount, f"hardware_overrides.gpu_vram_gb.{key}", minimum=0.01)
    result["gpu_vram_gb"] = vram

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
    directory = _required_string(value.get("dir", "logs"), "logging.dir")
    return {
        "level": level,
        "mode": mode,
        "dir": directory,
        "keep_days": _integer(value.get("keep_days", 7), "logging.keep_days", minimum=0),
        "max_size_mb": _integer(value.get("max_size_mb", 5), "logging.max_size_mb", minimum=1),
        "max_backups": _integer(value.get("max_backups", 5), "logging.max_backups", minimum=0),
        "console": _boolean(value.get("console", True), "logging.console"),
    }


def _public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    active = _mapping(config)
    dashboard = _mapping(active.get("dashboard"))
    off_peak = _mapping(dashboard.get("off_peak_badge"))
    vibe = _mapping(dashboard.get("vibe_coding"))
    ring = _mapping(vibe.get("ring"))
    model_bars = _mapping(vibe.get("model_bars"))

    balances: list[dict[str, Any]] = []
    raw_balances = vibe.get("balances", [])
    if isinstance(raw_balances, list):
        for item in raw_balances:
            if not isinstance(item, Mapping):
                continue
            balances.append({
                "provider": item.get("provider", ""),
                "name": item.get("name", ""),
                "color": item.get("color", "#888888"),
                "enabled": item.get("enabled", True),
            })

    local = _mapping(active.get("local_platforms"))
    urls: list[dict[str, Any]] = []
    raw_urls = local.get("urls", [])
    if isinstance(raw_urls, list):
        for item in raw_urls:
            url = _entry_url(item)
            if not url:
                continue
            urls.append({
                "url": url,
                "original_url": url,
                "password": _secret_view(_entry_password(item)),
            })

    hardware = _mapping(active.get("hardware_overrides"))
    logging_config = _mapping(active.get("logging"))
    return {
        "dashboard": {
            "token": _secret_view(dashboard.get("token")),
            "off_peak_badge": {
                "enabled": off_peak.get("enabled", True),
                "ranges": copy.deepcopy(off_peak.get("ranges", [{"start": "00:00", "end": "08:00"}])),
            },
            "vibe_coding": {
                "ring": {
                    "provider": ring.get("provider") or "",
                    "item": ring.get("item") or "",
                },
                "model_bars": {"provider": model_bars.get("provider") or ""},
                "balances": balances,
            },
        },
        "github_token": _secret_view(active.get("github_token")),
        "local_platforms": {
            "enabled": bool(local.get("enabled", False)),
            "username": local.get("username", "") or "",
            "password": _secret_view(local.get("password")),
            "urls": urls,
        },
        "nug": {
            "enabled": bool(_mapping(active.get("nug")).get("enabled", False)),
            "url": _mapping(active.get("nug")).get("url", "") or "",
            "username": _mapping(active.get("nug")).get("username", "") or "",
            "password": _secret_view(_mapping(active.get("nug")).get("password")),
        },
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
    }


def get_settings_options() -> dict[str, Any]:
    """返回不触发外部网络请求的表单选项。"""
    from providers import get_providers

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
    }


def get_settings_payload() -> dict[str, Any]:
    return {"config": _public_config(load_config()), "options": get_settings_options()}


def _update_secret(config: dict[str, Any], secrets: Mapping[str, Any], key: str, current: Any) -> Any:
    if key not in _SECRET_KEYS:
        raise SettingsValidationError("不允许修改该敏感字段", key)
    action, value = _secret_action(secrets.get(key), f"secrets.{key}")
    if action == "keep":
        return current
    return "" if action == "clear" else value


def _find_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get(name)
    if not isinstance(section, dict):
        section = {}
        config[name] = section
    return section


def save_settings_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验并保存后台提交的配置，返回应用结果摘要。"""
    if not isinstance(payload, Mapping):
        raise SettingsValidationError("请求体必须是对象")
    incoming = payload.get("config", {})
    secrets = payload.get("secrets", {})
    if not isinstance(incoming, Mapping):
        raise SettingsValidationError("config 必须是对象")
    if not isinstance(secrets, Mapping):
        raise SettingsValidationError("secrets 必须是对象")

    current = copy.deepcopy(load_config())
    next_config = copy.deepcopy(current)

    if "dashboard" in incoming:
        raw_dashboard = incoming.get("dashboard")
        if not isinstance(raw_dashboard, Mapping):
            raise SettingsValidationError("必须是对象", "dashboard")
        dashboard = _find_section(next_config, "dashboard")
        if "off_peak_badge" in raw_dashboard:
            dashboard["off_peak_badge"] = _validate_off_peak(raw_dashboard["off_peak_badge"])
        if "vibe_coding" in raw_dashboard:
            dashboard["vibe_coding"] = _validate_vibe(raw_dashboard["vibe_coding"])
        dashboard["token"] = _update_secret(
            next_config, secrets, "dashboard.token", _mapping(current.get("dashboard")).get("token", "")
        )

    if "github_token" in secrets:
        next_config["github_token"] = _update_secret(
            next_config, secrets, "github_token", current.get("github_token", "")
        )

    if "local_platforms" in incoming:
        raw_local = incoming.get("local_platforms")
        if not isinstance(raw_local, Mapping):
            raise SettingsValidationError("必须是对象", "local_platforms")
        local = _find_section(next_config, "local_platforms")
        if "enabled" in raw_local:
            local["enabled"] = _boolean(raw_local["enabled"], "local_platforms.enabled")
        if "username" in raw_local:
            local["username"] = raw_local["username"] if isinstance(raw_local["username"], str) else _required_string(
                raw_local["username"], "local_platforms.username"
            )
        if "urls" in raw_local:
            local["urls"] = _validate_local_urls(
                raw_local["urls"],
                _mapping(current.get("local_platforms")).get("urls", []),
                secrets.get("local_platforms.url_passwords", []),
            )
        local["password"] = _update_secret(
            next_config,
            secrets,
            "local_platforms.password",
            _mapping(current.get("local_platforms")).get("password", ""),
        )

    if "nug" in incoming:
        raw_nug = incoming.get("nug")
        if not isinstance(raw_nug, Mapping):
            raise SettingsValidationError("必须是对象", "nug")
        nug = _find_section(next_config, "nug")
        if "enabled" in raw_nug:
            nug["enabled"] = _boolean(raw_nug["enabled"], "nug.enabled")
        if "url" in raw_nug:
            nug["url"] = _http_url(raw_nug["url"], "nug.url")
        if "username" in raw_nug:
            nug["username"] = raw_nug["username"] if isinstance(raw_nug["username"], str) else _required_string(
                raw_nug["username"], "nug.username"
            )
        nug["password"] = _update_secret(
            next_config, secrets, "nug.password", _mapping(current.get("nug")).get("password", "")
        )

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

    save_config(next_config)
    applied, errors = apply_runtime_config()
    return {
        "ok": True,
        "applied": applied,
        "errors": errors,
        "config": _public_config(load_config()),
        "options": get_settings_options(),
    }


def reveal_secret(path: str) -> str:
    """按白名单读取单个敏感值；调用方必须先完成回环和 POST 防护。"""
    if not isinstance(path, str):
        raise SettingsValidationError("字段路径无效")
    config = load_config()
    if path == "dashboard.token":
        return str(_mapping(config.get("dashboard")).get("token", "") or "")
    if path == "github_token":
        return str(config.get("github_token", "") or "")
    if path == "local_platforms.password":
        return str(_mapping(config.get("local_platforms")).get("password", "") or "")
    if path == "nug.password":
        return str(_mapping(config.get("nug")).get("password", "") or "")
    match = re.fullmatch(r"local_platforms\.urls\[(\d+)\]\.password", path)
    if match:
        index = int(match.group(1))
        urls = _mapping(config.get("local_platforms")).get("urls", [])
        if isinstance(urls, list) and 0 <= index < len(urls):
            return _entry_password(urls[index]) or ""
    raise SettingsValidationError("不允许查看该敏感字段", path)


def apply_runtime_config() -> tuple[list[str], list[str]]:
    """通知各模块清理配置相关缓存；单个模块失败不阻止其它模块刷新。"""
    applied: list[str] = []
    errors: list[str] = []
    hooks: list[tuple[str, Any]] = []

    try:
        from providers import invalidate_data_cache
        hooks.append(("providers", invalidate_data_cache))
    except Exception as exc:
        errors.append(f"providers: {exc}")
    try:
        from providers.local_platform import reload_config as reload_local
        hooks.append(("local_platform", reload_local))
    except Exception as exc:
        errors.append(f"local_platform: {exc}")
    try:
        from providers.nug import reload_config as reload_nug
        hooks.append(("nug", reload_nug))
    except Exception as exc:
        errors.append(f"nug: {exc}")
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
