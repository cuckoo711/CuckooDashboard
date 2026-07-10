"""MiMo API access and dashboard data aggregation."""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timedelta, timezone

try:
    from mimo_usage import MiMoAPI, load_cookies, refresh_mimo_cookie, save_cookies
except ImportError as exc:
    raise RuntimeError("无法导入 mimo_usage.py，请确保文件存在") from exc

from services.cache import TTLCache
from services.local_platform_service import aggregate_local_usage

CACHE_TTL = 55  # 缓存55秒（前端60秒刷新）
_mimo_cache = TTLCache(CACHE_TTL)
_mimo_cookie_valid = None  # None=未检测, True=有效, False=过期
_mimo_cookie_last_check = 0  # 上次检测时间戳


def get_mimo_api() -> MiMoAPI | None:
    """获取 MiMoAPI 实例，自动检测 Cookie 有效性。
    过期时尝试用 passToken 自动刷新，刷新失败才返回 None。
    """
    global _mimo_cookie_valid, _mimo_cookie_last_check
    cache_info = load_cookies()
    cookie_str = cache_info.get("cookie")
    if not cookie_str:
        print("[MiMo] 未找到 Cookie，请先运行 mimo_usage.py --login qr --save 登录", flush=True)
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
            print("[MiMo] Cookie 已过期，尝试自动刷新...", flush=True)
            new_cookie = refresh_mimo_cookie(cookie_str)
            if new_cookie:
                # 刷新成功，保存新 cookie
                save_cookies(new_cookie, cache_info.get("method", "qr"), {
                    k: v for k, v in cache_info.items()
                    if k not in ("cookie", "method", "saved_at")
                })
                api = MiMoAPI(new_cookie)
                _mimo_cookie_valid = True
                print("[MiMo] 自动刷新成功，已保存新 Cookie [OK]", flush=True)
            else:
                print("[MiMo] 自动刷新失败，请手动运行: python mimo_usage.py --login qr --save", flush=True)
                _mimo_cookie_valid = False
        else:
            _mimo_cookie_valid = True
            print("[MiMo] Cookie 有效 [OK]", flush=True)
    except Exception as e:
        print(f"[MiMo] Cookie 检测失败: {e}", flush=True)
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


def fetch_all_data() -> dict:
    """获取所有 MiMo 数据（带缓存）。"""
    cached = _mimo_cache.get()
    if cached:
        return cached

    try:
        api = get_mimo_api()
        if api is None:
            # Cookie 过期，返回最小数据 + 过期标记
            return _mimo_expired_payload()

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
        }

        return _mimo_cache.set(result)

    except Exception as e:
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }
