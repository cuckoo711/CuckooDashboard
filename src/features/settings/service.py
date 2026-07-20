"""Settings 应用服务：读取选项、组装 payload 与保存编排。"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from contracts.settings import (
    RuntimeApplyResult,
    SettingsOptions,
    SettingsPayload,
    SettingsSaveRequest,
    SettingsSaveResult,
)
from core.config import CONFIG_VERSION, load_config, save_config
from core.credentials import VaultConflict, VaultError
from providers import get_providers
from services.font_service import font_exists, list_fonts
from services.spectrum_service import list_capture_devices, load_music_offsets, request_capture_restart
from services.theme import THEMES

from . import persistence, runtime, schema

SettingsValidationError = schema.SettingsValidationError

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LOG_MODES = {"daily", "size"}
_FONT_SIZE_KEYS: dict[str, tuple[int, int, int]] = {
    "title": (4, 80, 16),
    "clock": (4, 80, 22),
    "date": (4, 80, 15),
    "card_head": (4, 80, 10),
    "card_foot": (4, 80, 10),
    "card_body": (4, 80, 10),
}
_FONT_OFFSET_RANGE = (-20, 20)


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
    enabled = schema.boolean(value.get("enabled", True), "dashboard.off_peak_badge.enabled")
    ranges = value.get("ranges", [])
    if not isinstance(ranges, list):
        raise SettingsValidationError("必须是列表", "dashboard.off_peak_badge.ranges")
    return {
        "enabled": enabled,
        "ranges": [
            _time_range(item, f"dashboard.off_peak_badge.ranges[{idx}]")
            for idx, item in enumerate(ranges)
        ],
    }


def _validate_vibe(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.vibe_coding")
    raw_ring = value.get("ring", {})
    raw_model = value.get("model_bars", {})
    if not isinstance(raw_ring, Mapping) or not isinstance(raw_model, Mapping):
        raise SettingsValidationError("数据源选择必须是对象", "dashboard.vibe_coding")

    ring = {
        "provider": schema.optional_string(
            raw_ring.get("provider"), "dashboard.vibe_coding.ring.provider"
        ),
        "item": schema.optional_string(raw_ring.get("item"), "dashboard.vibe_coding.ring.item"),
    }
    model_bars = {
        "provider": schema.optional_string(
            raw_model.get("provider"), "dashboard.vibe_coding.model_bars.provider"
        ),
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
        enabled = schema.boolean(raw.get("enabled", True), f"{field}.enabled")
        provider_raw = raw.get("provider")
        if not enabled and not provider_raw:
            continue
        provider = schema.required_string(provider_raw, f"{field}.provider")
        provider_key = provider.casefold()
        if provider_key in seen:
            raise SettingsValidationError("余额 Provider 不能重复", f"{field}.provider")
        seen.add(provider_key)
        name = schema.optional_string(raw.get("name"), f"{field}.name") or provider
        color = raw.get("color") or "#888888"
        if not isinstance(color, str) or not _COLOR_RE.fullmatch(color):
            raise SettingsValidationError("颜色必须为 #RRGGBB", f"{field}.color")
        balances.append({"provider": provider, "name": name, "color": color, "enabled": enabled})
    return {"ring": ring, "model_bars": model_bars, "balances": balances}


def _validate_font(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.font")
    enabled = schema.boolean(value.get("enabled", False), "dashboard.font.enabled")
    filename = schema.optional_string(value.get("filename"), "dashboard.font.filename")
    if filename and not font_exists(filename):
        raise SettingsValidationError("字体文件不存在，请先上传", "dashboard.font.filename")
    return {"enabled": enabled, "filename": filename or ""}


def _validate_font_size(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "dashboard.font_size")
    result: dict[str, Any] = {}
    title_text = schema.optional_string(value.get("title_text"), "dashboard.font_size.title_text")
    result["title_text"] = title_text or "Cuckoo Dashboard"
    for key, (lo, hi, default) in _FONT_SIZE_KEYS.items():
        field = f"dashboard.font_size.{key}"
        number = schema.integer(value.get(key, default), field, minimum=lo)
        if number > hi:
            raise SettingsValidationError(f"不能超过 {hi}px", field)
        result[key] = number
    field = "dashboard.font_size.offset"
    offset = schema.integer(value.get("offset", 0), field, minimum=_FONT_OFFSET_RANGE[0])
    if offset > _FONT_OFFSET_RANGE[1]:
        raise SettingsValidationError(f"不能超过 {_FONT_OFFSET_RANGE[1]}", field)
    result["offset"] = offset
    return result


def _validate_hardware(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides")
    result: dict[str, Any] = {}
    for key in ("cpu_model", "mem_name", "gpu_model"):
        raw = value.get(key)
        result[key] = (
            None if raw is None or raw == "" else schema.required_string(raw, f"hardware_overrides.{key}")
        )

    memory = value.get("mem_installed_gb")
    result["mem_installed_gb"] = (
        None
        if memory is None or memory == ""
        else schema.finite_number(memory, "hardware_overrides.mem_installed_gb", minimum=0.01)
    )

    raw_vram = value.get("gpu_vram_gb", {})
    if not isinstance(raw_vram, Mapping):
        raise SettingsValidationError("必须是对象", "hardware_overrides.gpu_vram_gb")
    result["gpu_vram_gb"] = {
        schema.required_string(name, "hardware_overrides.gpu_vram_gb"): schema.finite_number(
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
            schema.required_string(item, f"hardware_overrides.apu_device_ids[{idx}]")
            for idx, item in enumerate(raw_apu)
        ]
    return result


def _validate_logging(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError("必须是对象", "logging")
    level = schema.required_string(value.get("level", "INFO"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        raise SettingsValidationError("级别必须为 DEBUG、INFO、WARNING 或 ERROR", "logging.level")
    mode = schema.required_string(value.get("mode", "daily"), "logging.mode").lower()
    if mode not in _LOG_MODES:
        raise SettingsValidationError("模式必须为 daily 或 size", "logging.mode")
    return {
        "level": level,
        "mode": mode,
        "dir": schema.required_string(value.get("dir", "logs"), "logging.dir"),
        "keep_days": schema.integer(value.get("keep_days", 7), "logging.keep_days", minimum=0),
        "max_size_mb": schema.integer(value.get("max_size_mb", 5), "logging.max_size_mb", minimum=1),
        "max_backups": schema.integer(value.get("max_backups", 5), "logging.max_backups", minimum=0),
        "console": schema.boolean(value.get("console", True), "logging.console"),
    }


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
    if device not in allowed and not (device.startswith("sc:") or device.startswith("sd:")):
        raise SettingsValidationError("未知采集设备", "music.capture_device")
    bins = schema.integer(value.get("bins", 48), "music.bins", minimum=16)
    if bins > 96:
        raise SettingsValidationError("bins 不能超过 96", "music.bins")
    render_fps = schema.integer(value.get("render_fps", 0), "music.render_fps", minimum=0)
    if render_fps and not 12 <= render_fps <= 60:
        raise SettingsValidationError("render_fps 必须为 0（自动）或 12-60", "music.render_fps")
    render_bars = schema.integer(value.get("render_bars", 0), "music.render_bars", minimum=0)
    if render_bars and not 12 <= render_bars <= 96:
        raise SettingsValidationError("render_bars 必须为 0（自动）或 12-96", "music.render_bars")
    return {
        "spectrum_enabled": schema.boolean(value.get("spectrum_enabled", True), "music.spectrum_enabled"),
        "spectrum_offset_ms": int(
            schema.finite_number(value.get("spectrum_offset_ms", 40), "music.spectrum_offset_ms")
        ),
        "bins": bins,
        "render_fps": render_fps,
        "render_bars": render_bars,
        "capture_device": device,
    }


def get_settings_options() -> SettingsOptions:
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
    active = schema.mapping(config)
    dashboard = schema.mapping(active.get("dashboard"))
    off_peak = schema.mapping(dashboard.get("off_peak_badge"))
    vibe = schema.mapping(dashboard.get("vibe_coding"))
    ring = schema.mapping(vibe.get("ring"))
    model_bars = schema.mapping(vibe.get("model_bars"))
    raw_balances = vibe.get("balances", [])
    balances = (
        [
            {
                "provider": item.get("provider", ""),
                "name": item.get("name", ""),
                "color": item.get("color", "#888888"),
                "enabled": item.get("enabled", True),
            }
            for item in raw_balances
            if isinstance(item, Mapping)
        ]
        if isinstance(raw_balances, list)
        else []
    )
    font = schema.mapping(dashboard.get("font"))
    font_size = schema.mapping(dashboard.get("font_size"))
    hardware = schema.mapping(active.get("hardware_overrides"))
    logging_config = schema.mapping(active.get("logging"))
    return {
        "config_version": active.get("config_version", CONFIG_VERSION),
        "dashboard": {
            "token": schema.secret_view(persistence.global_vault_secret("dashboard_token")),
            "off_peak_badge": {
                "enabled": off_peak.get("enabled", True),
                "ranges": copy.deepcopy(
                    off_peak.get("ranges", [{"start": "00:00", "end": "08:00"}])
                ),
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
        "github_token": schema.secret_view(persistence.global_vault_secret("github_token")),
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


def get_settings_payload() -> SettingsPayload:
    config = load_config()
    return {
        "config": _public_global_config(config),
        "providers": schema.provider_panels(config),
        "options": get_settings_options(),
        "credential_revision": persistence.get_credential_revision(),
    }


def save_settings_payload(
    payload: SettingsSaveRequest | Mapping[str, Any],
) -> SettingsSaveResult:
    """校验并保存无秘密 YAML 配置与 DPAPI Vault 凭据。"""
    if not isinstance(payload, Mapping):
        raise SettingsValidationError("请求体必须是对象")
    incoming = payload.get("config", {})
    secrets = payload.get("secrets", {})
    if not isinstance(incoming, Mapping):
        raise SettingsValidationError("config 必须是对象")
    if not isinstance(secrets, Mapping):
        raise SettingsValidationError("secrets 必须是对象")
    raw_revision = payload.get("credential_revision")
    if raw_revision is not None and (
        isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 0
    ):
        raise SettingsValidationError("credential_revision 必须是非负整数")

    current = load_config()
    next_config = copy.deepcopy(current)
    vault_globals: dict[str, str] = {}
    vault_provider_secrets: dict[str, dict[str, Any]] = {}

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
        if "dashboard.token" in secrets and persistence.secret_changed(
            secrets.get("dashboard.token"), "secrets.dashboard.token"
        ):
            vault_globals["dashboard_token"] = persistence.global_secret_update(
                secrets,
                "dashboard.token",
                persistence.global_vault_secret("dashboard_token"),
            )
        dashboard.pop("token", None)

    if "github_token" in secrets and persistence.secret_changed(
        secrets.get("github_token"), "secrets.github_token"
    ):
        vault_globals["github_token"] = persistence.global_secret_update(
            secrets,
            "github_token",
            persistence.global_vault_secret("github_token"),
        )
    next_config.pop("github_token", None)

    if "hardware_overrides" in incoming:
        next_config["hardware_overrides"] = _validate_hardware(incoming["hardware_overrides"])
    if "logging" in incoming:
        next_config["logging"] = _validate_logging(incoming["logging"])

    for key in ("theme", "lyric_offset", "vibe_active"):
        if key not in incoming:
            continue
        if key == "theme":
            theme = schema.required_string(incoming[key], "theme")
            if theme not in {item["name"] for item in THEMES}:
                raise SettingsValidationError("主题不存在", "theme")
            next_config[key] = theme
        elif key == "lyric_offset":
            next_config[key] = schema.finite_number(incoming[key], "lyric_offset")
        else:
            next_config[key] = schema.boolean(incoming[key], "vibe_active")

    music_changed = False
    if "music" in incoming:
        validated_music = _validate_music(incoming["music"])
        old_music = load_music_offsets()
        next_config["music"] = validated_music
        music_changed = (
            old_music.get("capture_device") != validated_music.get("capture_device")
            or old_music.get("spectrum_enabled") != validated_music.get("spectrum_enabled")
        )

    incoming_providers = incoming.get("providers", {})
    if not isinstance(incoming_providers, Mapping):
        raise SettingsValidationError("必须是对象", "providers")
    provider_schemas = schema.provider_schema_map()
    providers_config = next_config.get("providers")
    if not isinstance(providers_config, dict):
        providers_config = {}
        next_config["providers"] = providers_config
    for config_key, raw_provider in incoming_providers.items():
        if config_key not in provider_schemas:
            raise SettingsValidationError(
                "未注册或未声明配置 Schema 的 Provider", f"providers.{config_key}"
            )
        provider_name, provider_schema = provider_schemas[config_key]
        current_provider = schema.get_provider_config(
            provider_name, providers_config.get(config_key, {})
        )
        built = schema.build_provider_config(
            provider_name,
            provider_schema,
            raw_provider,
            current_provider,
            secrets,
            persistence.apply_secret_update,
        )
        secret_state = persistence.extract_provider_secret_state(provider_schema, built)
        # Provider configs always carry secret runtime values for validation, but
        # the DPAPI vault should only be rewritten when the client actually
        # sets/clears a secret. Keep actions must not bump credential_revision.
        if persistence.provider_secret_updates_requested(
            provider_schema, secrets, config_key=config_key
        ):
            vault_provider_secrets[provider_name] = secret_state
        providers_config[config_key] = built

    next_config["config_version"] = CONFIG_VERSION
    # Only touch the DPAPI vault when secret fields actually change. Ordinary
    # Settings edits (music, logging, themes, layout-related values) should not
    # compete with auth/refresh revision updates.
    if vault_globals or vault_provider_secrets:
        try:
            persistence.persist_vault_changes(
                vault_globals,
                vault_provider_secrets,
                expected_revision=raw_revision,
            )
        except VaultConflict as exc:
            raise SettingsValidationError(
                "凭据已被其他认证页面或刷新任务更新，请刷新设置页后重试"
            ) from exc
        except VaultError as exc:
            raise SettingsValidationError(
                "无法写入 Windows 凭据 Vault，请重新认证或检查当前用户权限"
            ) from exc

    save_config(next_config)
    if music_changed:
        request_capture_restart("settings save")
    applied, errors = runtime.apply_runtime_config()
    runtime_result: RuntimeApplyResult = {"applied": applied, "errors": errors}
    return {
        "ok": True,
        **runtime_result,
        **get_settings_payload(),
    }
