"""项目配置加载与路径常量。

配置文件：config/config.yaml
UI 运行时状态（theme、lyric_offset、vibe_active）统一存储在配置文件中。
cookies 由各插件自行管理。
"""

from __future__ import annotations

import copy
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
CONFIG_VERSION = 2
_MIGRATION_BACKUP = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.pre-providers.bak")
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
    """将旧版配置转换为 ``config_version=2`` 的 canonical 结构。

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

    # MiMo 的认证凭据仍由 cookies.json 管理，但启用状态属于 Provider 配置。
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


def _backup_before_migration(text: str) -> None:
    """首次自动迁移前保留原始配置副本，不覆盖已有备份。"""
    if _MIGRATION_BACKUP.exists():
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _MIGRATION_BACKUP.write_text(text, encoding="utf-8")
    except OSError:
        pass


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
            text = CONFIG_FILE.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                _config_cache, migrated = migrate_config(data)
                if migrated:
                    _backup_before_migration(text)
                    try:
                        save_config(_config_cache)
                        mtime = CONFIG_FILE.stat().st_mtime
                    except OSError:
                        # 内存中仍使用 canonical 配置；下次加载会再次尝试落盘。
                        pass
            else:
                _config_cache = {}
        except (yaml.YAMLError, OSError):
            _config_cache = {}
    else:
        _config_cache = {}

    _config_mtime = mtime
    return _config_cache


def save_config(config: dict) -> None:
    """将配置写回磁盘（YAML 格式）。"""
    global _config_cache, _config_mtime
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = yaml.dump(
        config,
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

    _config_cache = config
    try:
        _config_mtime = CONFIG_FILE.stat().st_mtime
    except OSError:
        _config_mtime = 0


def get_config_section(name: str, default: Any = None) -> Any:
    """Return a top-level config section."""
    config = load_config()
    value = config.get(name, default)
    return default if value is None else value


def get_provider_config(name: str, default: Any = None) -> Any:
    """读取 canonical 配置中的 ``providers.<name>``。"""
    providers = load_config().get("providers", {})
    if not isinstance(providers, dict):
        return default
    value = providers.get(name, default)
    return default if value is None else value


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
