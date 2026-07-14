"""NUG (NarraFork) 平台 provider — 入口模块。

CAPABILITIES: balance, api_usage
提供 NUG 平台的余额查询和按渠道用量统计。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.config import get_provider_config
from providers.nug.client import NUGClient

logger = logging.getLogger("cuckoo.providers.nug")

CAPABILITIES = ["balance", "api_usage"]

CONFIG_SCHEMA = {
    "config_key": "nug",
    "title": "NUG",
    "description": "NarraFork 平台余额和渠道用量配置。",
    "order": 30,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": False},
        {"key": "url", "label": "服务地址", "type": "url", "default": ""},
        {"key": "username", "label": "用户名", "type": "string", "default": ""},
        {"key": "password", "label": "密码", "type": "secret", "default": ""},
    ],
}

_client: NUGClient | None = None
_client_initialized = False
_last_success_at: str | None = None
_last_error: str | None = None


def _get_client() -> NUGClient | None:
    """获取或创建 NUG 客户端单例。"""
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    nug = get_provider_config("nug", {})
    if not nug.get("enabled"):
        return None
    url = nug.get("url", "")
    username = nug.get("username", "")
    password = nug.get("password", "")
    if not all([url, username, password]):
        return None
    _client = NUGClient(url, username, password)
    return _client


def reload_config() -> None:
    """清理 NUG 客户端单例，让下一次调用按最新配置登录。"""
    global _client, _client_initialized, _last_success_at, _last_error
    _client = None
    _client_initialized = False
    _last_success_at = None
    _last_error = None


# ============================================================
# balance 相关
# ============================================================


def get_balance() -> dict | None:
    """NUG 余额。

    返回统一格式:
    {
        "balance": str,
        "currency": "USD",
        "details": {}
    }
    """
    global _last_success_at, _last_error
    client = _get_client()
    if client is None:
        return None
    data = client.get_balance()
    if data is None:
        _last_error = "获取余额失败"
        return None
    _last_error = None
    _last_success_at = datetime.now(timezone.utc).isoformat()
    return {
        "balance": str(data.get("balance", 0)),
        "currency": "USD",
        "details": {},
    }


# ============================================================
# api_usage 相关
# ============================================================


def get_usage_summary() -> dict | None:
    """NUG 不提供汇总统计，返回 None。"""
    return None


def get_channel_breakdown(days: int = 7) -> list | None:
    """按 channel 分组用量。

    返回 rows 格式（附加插件自身的货币元数据）:
    [{"groupKey": str, "totalQuotaCost": str, "requestCount": int, "currency": "USD"}, ...]
    """
    global _last_success_at, _last_error
    client = _get_client()
    if client is None:
        return None
    rows = client.get_channel_breakdown(days=days)
    if rows is None:
        _last_error = "获取 channel breakdown 失败"
        return None
    _last_error = None
    _last_success_at = datetime.now(timezone.utc).isoformat()
    return [
        {**row, "currency": row.get("currency", "USD")}
        if isinstance(row, dict) else row
        for row in rows
    ]


# ============================================================
# 通用
# ============================================================


def get_status() -> dict:
    """插件状态。"""
    nug_config = get_provider_config("nug", {})
    enabled = bool(nug_config.get("enabled"))
    if not enabled:
        status = "disabled"
    elif _last_error:
        status = "error"
    elif _last_success_at:
        status = "ok"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "error": _last_error,
        "last_success_at": _last_success_at,
    }
