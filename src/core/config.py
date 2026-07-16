"""项目配置存储与路径常量。

本模块只负责 schema v4 YAML 的读取、验证与原子保存；它不发现 Provider、
不解释 Provider 字段，也不访问凭据 Vault。
"""

from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
from contextlib import contextmanager
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
# v4 is the only supported on-disk configuration schema.
CONFIG_VERSION = 4
CONFIG_LOCK_FILE = CONFIG_DIR / ".config-write.lock"

_file_lock_thread_guard = threading.RLock()


class ConfigError(RuntimeError):
    """配置文件不符合当前运行时要求。"""


class ConfigVersionError(ConfigError):
    """磁盘配置不是当前唯一支持的 schema 版本。"""


@contextmanager
def exclusive_file_lock(path: Path, *, timeout: float = 10.0):
    """以跨进程 advisory lock 串行化同一配置文件的写入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with _file_lock_thread_guard:
        with open(path, "a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            acquired = False
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - non-Windows test support
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"等待配置写入锁超时: {path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover - non-Windows test support
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


# ── 内存缓存 ──
_config_cache: dict[str, Any] | None = None
_config_mtime: float = 0


def _default_config() -> dict[str, Any]:
    """返回尚未创建 config.yaml 时使用的最小 v4 配置。"""
    return {"config_version": CONFIG_VERSION, "providers": {}}


def _validate_v4_config(config: Any, *, source: str) -> dict[str, Any]:
    """验证配置已是 v4；绝不转换或改写旧结构。"""
    if not isinstance(config, dict):
        raise ConfigError(f"{source} 必须是 YAML 对象")
    version = config.get("config_version")
    if version != CONFIG_VERSION:
        raise ConfigVersionError(
            f"{source} 仅支持 config_version: {CONFIG_VERSION}；"
            "旧配置不会自动迁移，请在设置页和 Provider 认证页重新配置后再启动。"
        )
    if not isinstance(config.get("providers"), dict):
        raise ConfigError(f"{source} 的 providers 必须是对象")
    return copy.deepcopy(config)


def _read_config_file() -> dict[str, Any]:
    """读取已有 YAML；缺失文件使用新安装的最小 v4 默认值。"""
    if not CONFIG_FILE.exists():
        return _default_config()
    try:
        parsed = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise ConfigError(f"无法读取 {CONFIG_FILE}: {exc}") from exc
    return _validate_v4_config(parsed, source=str(CONFIG_FILE))


def load_config() -> dict[str, Any]:
    """加载已验证的 v4 YAML 配置（带 mtime 缓存）。"""
    global _config_cache, _config_mtime

    try:
        mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0
    except OSError:
        mtime = 0
    if _config_cache is not None and mtime == _config_mtime:
        return _config_cache

    _config_cache = _read_config_file()
    _config_mtime = mtime
    return _config_cache


def save_config(config: dict[str, Any]) -> None:
    """原子写回已验证的 schema v4 YAML，不进行任何版本升级。"""
    global _config_cache, _config_mtime

    validated = _validate_v4_config(config, source="待保存配置")
    text = yaml.dump(
        validated,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    with exclusive_file_lock(CONFIG_LOCK_FILE):
        temporary_path: Path | None = None
        try:
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

            deadline = time.monotonic() + 10.0
            while True:
                try:
                    os.replace(temporary_path, CONFIG_FILE)
                    temporary_path = None
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    _config_cache = validated
    try:
        _config_mtime = CONFIG_FILE.stat().st_mtime
    except OSError:
        _config_mtime = 0


def invalidate_config_cache() -> None:
    """清空 v4 配置缓存。"""
    global _config_cache, _config_mtime
    _config_cache = None
    _config_mtime = 0


def get_config_section(name: str, default: Any = None) -> Any:
    """返回顶层配置项。"""
    value = load_config().get(name, default)
    return default if value is None else value


def set_config_value(key: str, value: Any) -> None:
    """更新单个顶层配置项并保存当前 v4 配置。"""
    config = copy.deepcopy(load_config())
    config[key] = value
    save_config(config)
