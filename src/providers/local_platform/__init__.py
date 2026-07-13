"""本地平台 provider — 入口模块。

CAPABILITIES: token_plan
提供本地 MiMo 兼容平台的今日 token 用量聚合。
"""

from __future__ import annotations

import logging
import time

from core.config import load_config
from providers.local_platform.client import LocalMimoAPI

logger = logging.getLogger("cuckoo.providers.local_platform")

CAPABILITIES = ["token_plan"]

_local_apis: list[LocalMimoAPI] | None = None
_last_success_at: str | None = None
_last_error: str | None = None
_last_available_count: int | None = None


def _get_apis() -> list[LocalMimoAPI]:
    """获取所有已配置的本地平台 API 实例（单例）。"""
    global _local_apis
    if _local_apis is not None:
        return _local_apis
    _local_apis = []
    config = load_config()
    lp = config.get("local_platforms", {})
    if not lp.get("enabled"):
        return _local_apis
    username = lp.get("username", "")
    default_password = lp.get("password", "")
    urls = lp.get("urls", [])
    if not all([username, default_password, urls]):
        return _local_apis
    for entry in urls:
        if isinstance(entry, dict):
            url = entry.get("url", "")
            pwd = entry.get("password", default_password)
        else:
            url = entry
            pwd = default_password
        if url:
            _local_apis.append(LocalMimoAPI(url, username, pwd))
    logger.info(f"[local] 已配置 {len(_local_apis)} 个本地平台")
    return _local_apis


def _empty_usage() -> dict:
    return {
        "requestCount": 0,
        "totalInputTokens": 0,
        "totalOutputTokens": 0,
        "totalCacheReadTokens": 0,
        "totalTokens": 0,
        "totalReasoningTokens": 0,
        "totalCost": 0,
        "errorCount": 0,
        "meterUsage": 0,
    }


# ============================================================
# token_plan 相关
# ============================================================


def get_plan_detail() -> dict | None:
    """本地平台无套餐概念，返回 None。"""
    return None


def get_plan_usage() -> dict | None:
    """本地平台无套餐用量，返回 None。"""
    return None


def get_daily_detail(year: int = None, month: int = None) -> dict | None:
    """本地平台不提供每日明细，返回 None。"""
    return None


def get_model_breakdown() -> list | None:
    """本地平台不提供按模型明细，返回 None。"""
    return None


def aggregate_today_usage() -> dict | None:
    """聚合所有本地平台今日 token 用量。

    这是本地平台的核心能力，返回格式同旧的 aggregate_local_usage()。
    无可用数据时返回 None。
    """
    global _last_success_at, _last_error, _last_available_count
    local_usage = _empty_usage()
    available_count = 0
    try:
        for api in _get_apis():
            today = api.get_today_usage()
            if today:
                available_count += 1
                local_usage["requestCount"] += today.get("requestCount", 0)
                local_usage["totalInputTokens"] += today.get("totalInputTokens", 0)
                local_usage["totalOutputTokens"] += today.get("totalOutputTokens", 0)
                local_usage["totalCacheReadTokens"] += today.get("totalCacheReadTokens", 0)
                local_usage["totalTokens"] += today.get("totalTokens", 0)
                local_usage["totalReasoningTokens"] += today.get("totalReasoningTokens", 0)
                local_usage["totalCost"] += today.get("totalCost", 0)
                local_usage["errorCount"] += today.get("errorCount", 0)
                local_usage["meterUsage"] += today.get("meterUsage", 0)
        _last_available_count = available_count
        _last_error = None if available_count else "未获取到本地平台数据"
        if available_count:
            _last_success_at = str(time.time())
            return local_usage
        return None
    except Exception as e:
        _last_available_count = 0
        _last_error = str(e)
        return None


# ============================================================
# 通用
# ============================================================


def get_status() -> dict:
    """插件状态。"""
    lp = load_config().get("local_platforms", {})
    enabled = bool(lp.get("enabled"))
    configured = len(lp.get("urls", []) or []) if enabled else 0
    if not enabled:
        status = "disabled"
    elif _last_available_count is None:
        status = "unknown"
    elif _last_available_count > 0:
        status = "ok"
    else:
        status = "error"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "error": _last_error,
        "last_success_at": _last_success_at,
    }
