"""统一日志系统配置。

根据 config.yaml 中的 logging 段初始化日志：
- 支持 daily（按天切割）和 size（按大小轮转）两种模式
- 分文件输出：app.log（INFO+）、error.log（ERROR+）
- 可选 console 输出到 stderr
- 自动清理过期日志文件
"""

from __future__ import annotations

import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT

_DEFAULT_CONFIG = {
    "level": "INFO",
    "mode": "daily",
    "dir": "logs",
    "keep_days": 7,
    "max_size_mb": 5,
    "max_backups": 5,
    "console": True,
}

_LOG_FORMAT = "%(asctime)s %(name)s [%(levelname)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_setup_done = False


def _resolve_log_dir(dir_value: str) -> Path:
    """将配置中的 dir 解析为绝对路径（相对于项目根目录）。"""
    p = Path(dir_value)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


def _daily_namer(default_name: str) -> str:
    """让 TimedRotatingFileHandler 生成 app.2026-07-14.log 格式的文件名。"""
    # default_name 形如 /path/to/app.log.2026-07-14
    dir_name = os.path.dirname(default_name)
    base_name = os.path.basename(default_name)
    # 分离：app.log.2026-07-14 → (app, .log.2026-07-14)
    # 我们想要：app.2026-07-14.log
    parts = base_name.split(".", 1)
    if len(parts) == 2:
        stem = parts[0]
        rest = parts[1]  # "log.2026-07-14"
        # 提取日期后缀
        match = re.search(r"(\d{4}-\d{2}-\d{2})", rest)
        if match:
            date_str = match.group(1)
            return os.path.join(dir_name, f"{stem}.{date_str}.log")
    return default_name


def _daily_rotator(source: str, dest: str):
    """配合 namer 执行实际的轮转重命名。"""
    if os.path.exists(source):
        if os.path.exists(dest):
            os.remove(dest)
        os.rename(source, dest)


def _cleanup_old_logs(log_dir: Path, keep_days: int):
    """删除超过 keep_days 天的 .log 文件。"""
    if keep_days <= 0 or not log_dir.exists():
        return
    cutoff = time.time() - keep_days * 86400
    date_pattern = re.compile(r"\.\d{4}-\d{2}-\d{2}\.log$")
    for f in log_dir.iterdir():
        if not f.is_file():
            continue
        if not date_pattern.search(f.name):
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


class _DailyRotatingHandler(TimedRotatingFileHandler):
    """按天轮转并在每次轮转后清理过期文件。

    自定义 namer 生成 app.2026-07-14.log，而基类的 backupCount 清理只匹配
    app.log.* 前缀，永远删不到重命名后的文件；必须自己在轮转时清理，
    否则长期运行的进程日志会无限累积（启动时的清理只跑一次）。
    """

    def __init__(self, *args: Any, cleanup_dir: Path, keep_days: int, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._cleanup_dir = cleanup_dir
        self._keep_days = keep_days

    def doRollover(self) -> None:
        super().doRollover()
        _cleanup_old_logs(self._cleanup_dir, self._keep_days)


def _create_daily_handler(
    log_path: Path, level: int, keep_days: int
) -> TimedRotatingFileHandler:
    handler = _DailyRotatingHandler(
        filename=str(log_path),
        when="midnight",
        encoding="utf-8",
        cleanup_dir=log_path.parent,
        keep_days=keep_days,
    )
    handler.namer = _daily_namer
    handler.rotator = _daily_rotator
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    return handler


def _create_size_handler(
    log_path: Path, level: int, max_size_mb: int, max_backups: int
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=max_backups,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    return handler


def setup_logging(config: dict[str, Any] | None = None) -> None:
    """根据配置初始化日志系统。应在应用启动时调用一次。"""
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    cfg = dict(_DEFAULT_CONFIG)
    if config and isinstance(config.get("logging"), dict):
        cfg.update(config["logging"])

    # 解析参数
    level_name = cfg.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    mode = cfg.get("mode", "daily")
    log_dir = _resolve_log_dir(cfg.get("dir", "logs"))
    keep_days = int(cfg.get("keep_days", 7))
    max_size_mb = int(cfg.get("max_size_mb", 5))
    max_backups = int(cfg.get("max_backups", 5))
    console = cfg.get("console", True)

    # 确保日志目录存在
    log_dir.mkdir(parents=True, exist_ok=True)

    # 配置 root logger
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler（避免重复）
    root.handlers.clear()

    app_log = log_dir / "app.log"
    error_log = log_dir / "error.log"

    # 主日志文件（INFO+）
    if mode == "size":
        root.addHandler(_create_size_handler(app_log, level, max_size_mb, max_backups))
        root.addHandler(
            _create_size_handler(error_log, logging.ERROR, max_size_mb, max_backups)
        )
    else:
        # daily 模式（默认）
        root.addHandler(_create_daily_handler(app_log, level, keep_days))
        root.addHandler(_create_daily_handler(error_log, logging.ERROR, keep_days))
        # 启动时清理过期文件
        _cleanup_old_logs(log_dir, keep_days)

    # Console handler
    if console:
        import sys

        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(
            logging.Formatter("%(name)s %(message)s")
        )
        root.addHandler(console_handler)


def reload_logging(config: dict[str, Any] | None = None) -> None:
    """运行时重新应用日志配置。"""
    global _setup_done
    root = logging.getLogger()
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:
            pass
    root.handlers.clear()
    _setup_done = False
    setup_logging(config)
