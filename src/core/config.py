"""项目配置加载与路径常量。

配置文件：config/config.yaml
UI 运行时状态（theme、lyric_offset、vibe_active）统一存储在配置文件中。
所有持久化认证凭据由 core.credentials 的 Windows DPAPI Vault 管理。
"""

from __future__ import annotations

import copy
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

# src/ directory (where source code lives)
SRC_DIR = Path(__file__).resolve().parent.parent

# Project root (parent of src/)
PROJECT_ROOT = SRC_DIR.parent

# User-editable config directory
CONFIG_DIR = PROJECT_ROOT / "config"

# Runtime cache directory (auto-generated, user should not edit)
DATA_DIR = PROJECT_ROOT / "data"

CONFIG_FILE = CONFIG_DIR / "config.yaml"
CONFIG_VERSION = 3
logger = logging.getLogger("cuckoo.config")
_LEGACY_PROVIDER_KEYS = {
    "local_platforms": "local_platform",
    "nug": "nug",
}

# ── 内存缓存 ──
_config_cache: dict | None = None
_config_mtime: float = 0


def _merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    """将旧配置字段补入 canonical 配置，不覆盖用户已经迁移的值。"""
    for key, value in source.items():
        if key not in target:
            target[key] = copy.deepcopy(value)


def _normalize_local_platform_urls(section: dict[str, Any]) -> None:
    """将旧版字符串 URL 规范化为对象列表；读取端仍兼容字符串。"""
    urls = section.get("urls")
    if not isinstance(urls, list):
        return
    normalized: list[Any] = []
    for entry in urls:
        if isinstance(entry, str):
            normalized.append({"url": entry})
        elif isinstance(entry, dict):
            normalized.append(copy.deepcopy(entry))
        else:
            normalized.append(entry)
    section["urls"] = normalized


def migrate_config(config: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """将旧版配置转换为 ``config_version=3`` 的 canonical 结构。

    Provider 配置统一放在 ``providers.<provider_name>`` 下；其它全局配置保持
    原有顶层位置。函数不修改传入对象，并且可重复调用。
    """
    if not isinstance(config, dict):
        return {}, False

    migrated = copy.deepcopy(config)
    changed = False
    raw_providers = migrated.get("providers")
    if isinstance(raw_providers, dict):
        providers = raw_providers
    else:
        providers = {}
        if "providers" in migrated:
            changed = True

    for legacy_key, provider_name in _LEGACY_PROVIDER_KEYS.items():
        if legacy_key not in migrated:
            continue
        legacy_value = migrated.pop(legacy_key)
        changed = True
        if provider_name not in providers:
            providers[provider_name] = copy.deepcopy(legacy_value)
        elif isinstance(providers.get(provider_name), dict) and isinstance(legacy_value, dict):
            _merge_missing(providers[provider_name], legacy_value)

    local_config = providers.get("local_platform")
    if isinstance(local_config, dict):
        before_urls = copy.deepcopy(local_config.get("urls"))
        _normalize_local_platform_urls(local_config)
        if before_urls != local_config.get("urls"):
            changed = True

    # MiMo 的认证凭据由 DPAPI Vault 管理；YAML 仅保留 Provider 启用状态。
    if "mimo" not in providers:
        providers["mimo"] = {"enabled": True}
        changed = True
    elif isinstance(providers["mimo"], dict) and "enabled" not in providers["mimo"]:
        providers["mimo"]["enabled"] = True
        changed = True

    if migrated.get("config_version") != CONFIG_VERSION:
        migrated["config_version"] = CONFIG_VERSION
        changed = True
    if migrated.get("providers") != providers:
        migrated["providers"] = providers
        changed = True
    return migrated, changed


def load_config() -> dict[str, Any]:
    """加载 YAML 配置（带文件修改时间缓存）。"""
    global _config_cache, _config_mtime

    try:
        mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0
    except OSError:
        mtime = 0

    if _config_cache is not None and mtime == _config_mtime:
        return _config_cache

    if CONFIG_FILE.exists():
        try:
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                canonical, changed = migrate_config(data)
                cleanup_paths: list[Path] = []
                try:
                    # 凭据迁移先写入并校验 DPAPI Vault；只有成功后才清理 YAML/旧文件。
                    from core.credential_migration import migrate_legacy_credentials

                    canonical, credential_changed, cleanup_paths = migrate_legacy_credentials(canonical)
                    changed = changed or credential_changed
                except Exception as exc:
                    # 绝不因 Vault 不可用而删除唯一的旧明文来源；Provider 会报告 needs_login。
                    logger.error("凭据迁移未完成，保留旧来源: %s", exc)
                    cleanup_paths = []

                _config_cache = canonical
                saved = True
                if changed:
                    try:
                        save_config(canonical)
                        mtime = CONFIG_FILE.stat().st_mtime
                    except OSError:
                        saved = False
                        # 内存中仍使用 canonical 配置；下次加载会再次尝试落盘。
                if saved:
                    for legacy_path in cleanup_paths:
                        try:
                            legacy_path.unlink(missing_ok=True)
                        except OSError:
                            logger.warning("无法清理已迁移的旧凭据文件: %s", legacy_path)
            else:
                _config_cache = {}
        except (yaml.YAMLError, OSError):
            _config_cache = {}
    else:
        _config_cache = {}

    _config_mtime = mtime
    return _config_cache


def save_config(config: dict) -> None:
    """将配置写回磁盘（YAML 格式），并始终写入当前 schema 版本。"""
    global _config_cache, _config_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    canonical = copy.deepcopy(config)
    canonical["config_version"] = CONFIG_VERSION
    text = yaml.dump(
        canonical,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    temporary_path: Path | None = None
    try:
        # 在配置文件同一目录创建临时文件，再原子替换目标文件；这样读线程不会看到半写入内容。
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_DIR,
            prefix=f".{CONFIG_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, CONFIG_FILE)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    _config_cache = canonical
    try:
        _config_mtime = CONFIG_FILE.stat().st_mtime
    except OSError:
        _config_mtime = 0


def get_config_section(name: str, default: Any = None) -> Any:
    """Return a top-level config section."""
    config = load_config()
    value = config.get(name, default)
    return default if value is None else value


def _merge_provider_vault_secrets(provider_name: str, config: dict[str, Any]) -> dict[str, Any]:
    """将 Schema 声明的通用 secret 从 Vault 合并到运行时配置副本。"""
    try:
        from core.credentials import VaultError, get_provider_state
        from providers import get_provider_config_schema

        schema = get_provider_config_schema(provider_name)
        state = get_provider_state(provider_name, {})
    except Exception:
        return config
    if not isinstance(schema, dict) or not isinstance(state, dict):
        return config
    secret_state = state.get("config_secrets")
    if not isinstance(secret_state, dict):
        return config
    fields = secret_state.get("fields") if isinstance(secret_state.get("fields"), dict) else {}
    objects = secret_state.get("objects") if isinstance(secret_state.get("objects"), dict) else {}
    merged = copy.deepcopy(config)
    for spec in schema.get("fields", []):
        if not isinstance(spec, dict) or not isinstance(spec.get("key"), str):
            continue
        key = spec["key"]
        if spec.get("type") == "secret":
            if key in fields:
                merged[key] = fields[key]
            continue
        if spec.get("type") != "object_list":
            continue
        identity_key = spec.get("identity_key")
        rows = merged.get(key)
        scoped = objects.get(key) if isinstance(objects.get(key), dict) else {}
        if not isinstance(identity_key, str) or not isinstance(rows, list):
            continue
        item_fields = spec.get("item_fields", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = row.get(identity_key)
            saved = scoped.get(identity) if isinstance(identity, str) else None
            if not isinstance(saved, dict):
                continue
            for item_spec in item_fields if isinstance(item_fields, list) else []:
                if not isinstance(item_spec, dict) or item_spec.get("type") != "secret":
                    continue
                item_key = item_spec.get("key")
                if isinstance(item_key, str) and item_key in saved:
                    row[item_key] = saved[item_key]
    return merged


def get_provider_config(name: str, default: Any = None) -> Any:
    """读取运行时 Provider 配置，并合并 Schema 管理的 Vault secret。"""
    providers = load_config().get("providers", {})
    if not isinstance(providers, dict):
        return default
    value = providers.get(name, default)
    if value is None:
        return default
    if not isinstance(value, dict):
        return value
    return _merge_provider_vault_secrets(name, value)


def set_provider_config(name: str, value: Any) -> None:
    """更新 ``providers.<name>`` 并保存配置。"""
    config = copy.deepcopy(load_config())
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
    providers[name] = value
    save_config(config)


def set_config_value(key: str, value: Any) -> None:
    """更新配置中的单个顶层字段并保存。"""
    config = load_config().copy()
    config[key] = value
    save_config(config)
