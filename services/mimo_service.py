"""MiMo API access and dashboard data aggregation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

try:
    from mimo_usage import MiMoAPI, load_cookies, refresh_mimo_cookie, save_cookies
except ImportError as exc:
    raise RuntimeError("无法导入 mimo_usage.py，请确保文件存在") from exc

from services.cache import TTLCache
from services.local_platform_service import aggregate_local_usage

logger = logging.getLogger("cuckoo.mimo")

CACHE_TTL = 55  # 缓存55秒（前端60秒刷新）
_mimo_cache = TTLCache(CACHE_TTL)
_mimo_cookie_valid = None  # None=未检测, True=有效, False=过期
_mimo_cookie_last_check = 0  # 上次检测时间戳
_last_result: dict | None = None
_last_success_at: str | None = None
_last_error: str | None = None


def get_mimo_api() -> MiMoAPI | None:
    """获取 MiMoAPI 实例，自动检测 Cookie 有效性。
    过期时尝试用 passToken 自动刷新，刷新失败才返回 None。
    """
    global _mimo_cookie_valid, _mimo_cookie_last_check
    cache_info = load_cookies()
    cookie_str = cache_info.get("cookie")
    if not cookie_str:
        logger.info("[MiMo] 未找到 Cookie，请先运行 mimo_usage.py --login qr --save 登录")
        _mimo_cookie_valid = False
        return None

    api = MiMoAPI(cookie_str)

    # 每 5 分钟最多检测一次
    now = time.time()
    if _mimo_cookie_valid is not None and (now - _mimo_cookie_last_check) < 300:
        return api if _mimo_cookie_valid else None

    _mimo_cookie_last_check = now

    try:
        test = api.get_user_profile()
        if test.get("code") == 401:
            # Cookie 过期，尝试用 passToken 自动刷新
            logger.error("[MiMo] Cookie 已过期，尝试自动刷新...")
            new_cookie = refresh_mimo_cookie(cookie_str)
            if new_cookie:
                # 刷新成功，保存新 cookie
                save_cookies(new_cookie, cache_info.get("method", "qr"), {
                    k: v for k, v in cache_info.items()
                    if k not in ("cookie", "method", "saved_at")
                })
                api = MiMoAPI(new_cookie)
                _mimo_cookie_valid = True
                logger.info("[MiMo] 自动刷新成功，已保存新 Cookie [OK]")
            else:
                logger.error("[MiMo] 自动刷新失败，请手动运行: python mimo_usage.py --login qr --save")
                _mimo_cookie_valid = False
        else:
            _mimo_cookie_valid = True
            logger.info("[MiMo] Cookie 有效 [OK]")
    except Exception as e:
        logger.error(f"[MiMo] Cookie 检测失败: {e}")
        _mimo_cookie_valid = False

    return api if _mimo_cookie_valid else None


def _mimo_expired_payload() -> dict:
    return {
        "success": False,
        "mimo_expired": True,
        "profile": {},
        "plan": {},
        "usage": {},
        "balance": {},
        "payg_usage": {},
        "daily_detail": {},
        "tp_usage_detail": [],
        "local_usage": {},
        "mimo_inMiss": 0,
        "today": {"in": 0, "out": 0, "cache": 0, "total": 0, "inMiss": 0},
        "github": {},
        "system": {},
        "timestamp": time.time(),
    }


def _calculate_mimo_in_miss(daily_data: dict) -> int:
    """计算 MiMo 的 inMiss（非缓存输入）。"""
    tu = daily_data.get("tokenUsage", [])
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    ref = datetime.utcnow() if bj_now.hour >= 8 else datetime.utcnow() - timedelta(days=1)
    target_key = f"{ref.month:02d}-{ref.day:02d}"
    for t in tu:
        if t[0] == target_key:
            return max(0, t[1] - t[4])  # inTok - cache
    return 0


def _extract_today_tokens(daily_data: dict) -> dict:
    """从 daily_detail 提取今日 token 用量（按北京时间日界线）。
    返回 {"in": int, "out": int, "cache": int, "total": int}。"""
    tu = daily_data.get("tokenUsage", [])
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    ref = datetime.utcnow() if bj_now.hour >= 8 else datetime.utcnow() - timedelta(days=1)
    target_key = f"{ref.month:02d}-{ref.day:02d}"
    for t in tu:
        if t[0] == target_key:
            return {
                "in": t[1] or 0,
                "out": t[2] or 0,
                "total": t[3] or 0,
                "cache": t[4] or 0,
            }
    return {"in": 0, "out": 0, "total": 0, "cache": 0}


def fetch_all_data() -> dict:
    """获取所有 MiMo 数据（带缓存）。"""
    global _last_result, _last_success_at, _last_error
    cached = _mimo_cache.get()
    if cached:
        return cached

    try:
        api = get_mimo_api()
        if api is None:
            # Cookie 过期，返回最小数据 + 过期标记
            _last_result = _mimo_expired_payload()
            _last_error = "MiMo Cookie unavailable or expired"
            return _last_result

        # 获取按天明细（本月）- 先获取以避免 session 状态问题
        # 注意：MiMo 平台按 UTC（世界时）分组统计每日用量，
        # 北京时间 0:00-8:00 时 UTC 仍是前一天，需按 UTC 年月请求，
        # 否则月初这段时间会请求到还没有数据的新月份。
        utc_now = datetime.utcnow()
        year = utc_now.year
        month = utc_now.month
        daily_resp = api.session.get(
            f"https://platform.xiaomimimo.com/api/v1/usage/detail?year={year}&month={month}",
            timeout=15,
        )
        daily_detail = daily_resp.json()

        profile = api.get_user_profile()
        plan = api.get_token_plan_detail()
        usage = api.get_token_plan_usage()
        balance = api.get_balance()
        payg_usage = api.get_usage()
        tp_usage_detail = api.get_token_plan_usage_detail()

        daily_data = daily_detail.get("data", {})
        local_usage = aggregate_local_usage()
        mimo_in_miss = _calculate_mimo_in_miss(daily_data)
        mimo_today = _extract_today_tokens(daily_data)

        # 合并 MiMo + 本地平台今日用量
        lu_in = (local_usage or {}).get("totalInputTokens", 0)
        lu_out = (local_usage or {}).get("totalOutputTokens", 0)
        lu_cache = (local_usage or {}).get("totalCacheReadTokens", 0)
        lu_total = (local_usage or {}).get("totalTokens", 0)
        today = {
            "in": mimo_today["in"] + lu_in,
            "out": mimo_today["out"] + lu_out,
            "cache": mimo_today["cache"] + lu_cache,
            "total": mimo_today["total"] + lu_total,
            "inMiss": mimo_in_miss + lu_in,
        }

        result = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "profile": profile.get("data", {}),
            "plan": plan.get("data", {}),
            "usage": usage.get("data", {}),
            "balance": balance.get("data", {}),
            "payg_usage": payg_usage.get("data", {}),
            "tp_usage_detail": tp_usage_detail.get("data", []),
            "daily_detail": daily_data,
            "local_usage": local_usage,
            "mimo_inMiss": mimo_in_miss,
            "today": today,
        }

        _last_result = result
        _last_success_at = result["timestamp"]
        _last_error = None
        return _mimo_cache.set(result)

    except Exception as e:
        logger.exception("MiMo data fetch failed")
        _last_error = str(e)
        _last_result = {
            "success": False,
            "error": _last_error,
            "timestamp": datetime.now().isoformat(),
        }
        return _last_result


def get_mimo_status() -> dict:
    """Return the latest MiMo status without contacting the remote API."""
    if _last_result and _last_result.get("success"):
        status = "ok"
    elif _last_error or _mimo_cookie_valid is False:
        status = "error"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "stale": False,
        "error": _last_error,
        "last_success_at": _last_success_at,
        "details": {"cookie_checked": _mimo_cookie_valid is not None},
    }
