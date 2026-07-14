"""项目配置加载与路径常量。

配置文件：config/config.yaml
UI 运行时状态（theme、lyric_offset、vibe_active）统一存储在配置文件中。
cookies 由各插件自行管理。
"""

from __future__ import annotations

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

# ── 内存缓存 ──
_config_cache: dict | None = None
_config_mtime: float = 0


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
            _config_cache = data if isinstance(data, dict) else {}
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


def set_config_value(key: str, value: Any) -> None:
    """更新配置中的单个顶层字段并保存。"""
    config = load_config().copy()
    config[key] = value
    save_config(config)
