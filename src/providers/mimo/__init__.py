"""MiMo 官方平台 provider — 入口模块。

CAPABILITIES: token_plan, balance, api_usage
提供 MiMo 平台的套餐、余额、按量付费全部数据。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from core.config import get_provider_config
from providers.mimo.api import get_mimo_api, is_cookie_valid, reload_config as reload_api_config

logger = logging.getLogger("cuckoo.providers.mimo")

CAPABILITIES = ["token_plan", "balance", "api_usage"]

CONFIG_SCHEMA = {
    "config_key": "mimo",
    "title": "MiMo",
    "description": "MiMo 官方平台。认证 Cookie 由 mimo_usage.py 和 config/cookies.json 管理。",
    "order": 10,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
    ],
    "status_only_auth": True,
}

_last_success_at: str | None = None
_last_error: str | None = None


# ============================================================
# token_plan 相关
# ============================================================


def get_plan_detail() -> dict | None:
    """套餐详情（名称、到期、自动续费等）"""
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_token_plan_detail()
        return resp.get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_plan_detail 失败: {e}")
        return None


def get_plan_usage() -> dict | None:
    """套餐用量（monthUsage + usage，含 percent/items）"""
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_token_plan_usage()
        return resp.get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_plan_usage 失败: {e}")
        return None


def get_daily_detail(year: int = None, month: int = None) -> dict | None:
    """每日 token 明细。

    默认按 UTC 当前年月请求（避免北京时间凌晨跨月问题）。
    返回: {"tokenUsage": [[日期key, in, out, total, cache], ...], ...}
    """
    api = get_mimo_api()
    if api is None:
        return None
    try:
        if year is None or month is None:
            utc_now = datetime.utcnow()
            year = year or utc_now.year
            month = month or utc_now.month
        resp = api.session.get(
            f"https://platform.xiaomimimo.com/api/v1/usage/detail?year={year}&month={month}",
            timeout=15,
        )
        return resp.json().get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_daily_detail 失败: {e}")
        return None


def get_model_breakdown() -> list | None:
    """Token Plan 按模型月度明细列表。"""
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_token_plan_usage_detail()
        return resp.get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_model_breakdown 失败: {e}")
        return None


def get_user_profile() -> dict | None:
    """用户资料（额外能力，不属于标准 capability）。"""
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_user_profile()
        return resp.get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_user_profile 失败: {e}")
        return None


# ============================================================
# balance 相关
# ============================================================


def get_balance() -> dict | None:
    """账户余额。

    返回统一格式:
    {
        "balance": str,
        "currency": str,
        "details": {"cashBalance", "giftBalance", "frozenBalance"}
    }
    """
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_balance()
        data = resp.get("data", {})
        if not data:
            return None
        return {
            "balance": data.get("balance", "0"),
            "currency": data.get("currency", "CNY"),
            "details": {
                "cashBalance": data.get("cashBalance", "0"),
                "giftBalance": data.get("giftBalance", "0"),
                "frozenBalance": data.get("frozenBalance", "0"),
            },
        }
    except Exception as e:
        logger.error(f"[MiMo] get_balance 失败: {e}")
        return None


# ============================================================
# api_usage 相关
# ============================================================


def get_usage_summary() -> dict | None:
    """按量付费汇总（token、费用、请求数、限流）。"""
    api = get_mimo_api()
    if api is None:
        return None
    try:
        resp = api.get_usage()
        return resp.get("data")
    except Exception as e:
        logger.error(f"[MiMo] get_usage_summary 失败: {e}")
        return None


def get_channel_breakdown(days: int = 7) -> list | None:
    """MiMo 不提供按渠道分组，返回 None。"""
    return None


# ============================================================
# 通用
# ============================================================


def reload_config() -> None:
    """清理 MiMo Cookie 状态缓存，让启用状态立即生效。"""
    global _last_success_at, _last_error
    _last_success_at = None
    _last_error = None
    reload_api_config()


def get_status() -> dict:
    """插件状态。"""
    global _last_success_at, _last_error
    mimo_config = get_provider_config("mimo", {})
    enabled = bool(mimo_config.get("enabled", True)) if isinstance(mimo_config, dict) else True
    if not enabled:
        return {
            "status": "disabled",
            "ok": False,
            "enabled": False,
            "error": None,
            "last_success_at": _last_success_at,
        }
    cookie_valid = is_cookie_valid()
    if cookie_valid is True:
        status = "ok"
    elif cookie_valid is False:
        status = "error"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "error": _last_error,
        "last_success_at": _last_success_at,
    }
