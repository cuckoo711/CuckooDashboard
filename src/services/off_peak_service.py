"""顶部闲时倍率标签的配置解析。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from core.config import load_config

logger = logging.getLogger("cuckoo.off_peak")

_DEFAULT_RANGES = (
    {"start": "00:00", "end": "08:00"},
)
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _default_payload() -> dict[str, Any]:
    """返回与历史硬编码行为一致的默认配置。"""
    return {
        "enabled": True,
        "ranges": [dict(item) for item in _DEFAULT_RANGES],
    }


def _parse_minutes(value: Any) -> int | None:
    """将严格的 HH:MM 字符串转换为当天分钟数。"""
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        return None
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _normalize_ranges(raw_ranges: Any) -> list[dict[str, str]]:
    """过滤无效区间，保留可供前端直接判断的规范化区间。"""
    if not isinstance(raw_ranges, list):
        logger.warning("[off_peak] off_peak_badge.ranges 必须是列表，已忽略")
        return []

    ranges: list[dict[str, str]] = []
    for index, item in enumerate(raw_ranges):
        if not isinstance(item, Mapping):
            logger.warning("[off_peak] 第 %s 个闲时区间不是对象，已忽略", index + 1)
            continue

        start = item.get("start")
        end = item.get("end")
        start_minutes = _parse_minutes(start)
        end_minutes = _parse_minutes(end)
        if start_minutes is None or end_minutes is None:
            logger.warning("[off_peak] 第 %s 个闲时区间时间格式无效，已忽略", index + 1)
            continue
        if start_minutes == end_minutes:
            logger.warning("[off_peak] 第 %s 个闲时区间起止时间相同，已忽略", index + 1)
            continue

        ranges.append({"start": start, "end": end})
    return ranges


def build_off_peak_badge_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """从完整配置构建前端所需的闲时倍率标签配置。

    ``ranges`` 未配置时沿用旧版的 00:00–08:00；显式配置空列表则表示
    没有闲时区间。跨午夜区间（如 22:00–02:00）会原样保留，由前端判断。
    """
    default = _default_payload()
    if not isinstance(config, Mapping):
        return default

    dashboard = config.get("dashboard")
    if not isinstance(dashboard, Mapping):
        return default

    raw_badge = dashboard.get("off_peak_badge")
    if raw_badge is None:
        return default
    if not isinstance(raw_badge, Mapping):
        logger.warning("[off_peak] dashboard.off_peak_badge 必须是对象，已使用默认值")
        return default

    raw_enabled = raw_badge.get("enabled", True)
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    else:
        logger.warning("[off_peak] off_peak_badge.enabled 必须是布尔值，已使用 true")
        enabled = True

    if "ranges" not in raw_badge:
        ranges = default["ranges"]
    else:
        ranges = _normalize_ranges(raw_badge.get("ranges"))

    return {"enabled": enabled, "ranges": ranges}


def get_off_peak_badge_config() -> dict[str, Any]:
    """读取当前 YAML 配置并返回闲时倍率标签设置。"""
    return build_off_peak_badge_config(load_config())
