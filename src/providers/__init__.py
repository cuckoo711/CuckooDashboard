"""providers 自动发现与聚合模块。

扫描 providers/ 下所有含 __init__.py 的子目录作为插件，
按各插件声明的 CAPABILITIES 分类注册，提供统一的聚合调用。

同时提供 dashboard 级别的聚合函数（fetch_all_data 等）。
"""

from __future__ import annotations

import importlib
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

logger = logging.getLogger("cuckoo.providers")

_PROVIDERS_DIR = Path(__file__).parent
_registry: dict[str, ModuleType] = {}  # {插件名: 模块}
_discovered = False


def _discover() -> None:
    """扫描子目录，导入各插件模块。"""
    global _discovered
    if _discovered:
        return
    _discovered = True

    for item in sorted(_PROVIDERS_DIR.iterdir()):
        if not item.is_dir():
            continue
        if not (item / "__init__.py").exists():
            continue
        name = item.name
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"providers.{name}")
            if hasattr(mod, "CAPABILITIES"):
                _registry[name] = mod
                logger.info(f"[providers] 已加载插件: {name} -> {mod.CAPABILITIES}")
            else:
                logger.warning(f"[providers] 插件 {name} 缺少 CAPABILITIES 声明，已跳过")
        except Exception as e:
            logger.error(f"[providers] 加载插件 {name} 失败: {e}")


def get_providers() -> dict[str, ModuleType]:
    """返回所有已注册的插件 {名称: 模块}。"""
    _discover()
    return dict(_registry)


def get_providers_by_capability(capability: str) -> dict[str, ModuleType]:
    """返回声明了指定 capability 的插件。"""
    _discover()
    return {
        name: mod
        for name, mod in _registry.items()
        if capability in getattr(mod, "CAPABILITIES", [])
    }


def call_all(capability: str, method: str, *args, **kwargs) -> list[dict]:
    """调用所有拥有指定 capability 的插件的某个方法，收集结果。

    返回: [{"provider": 插件名, "data": 返回值}, ...]
    跳过未实现该方法或调用失败的插件。
    """
    _discover()
    results = []
    for name, mod in _registry.items():
        if capability not in getattr(mod, "CAPABILITIES", []):
            continue
        fn = getattr(mod, method, None)
        if fn is None:
            continue
        try:
            data = fn(*args, **kwargs)
            results.append({"provider": name, "data": data})
        except Exception as e:
            logger.error(f"[providers] {name}.{method}() 调用失败: {e}")
            results.append({"provider": name, "data": None, "error": str(e)})
    return results


def call_one(provider_name: str, method: str, *args, **kwargs):
    """调用指定插件的某个方法。找不到插件或方法时返回 None。"""
    _discover()
    mod = _registry.get(provider_name)
    if mod is None:
        return None
    fn = getattr(mod, method, None)
    if fn is None:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.error(f"[providers] {provider_name}.{method}() 调用失败: {e}")
        return None


# ============================================================
# Dashboard 聚合函数
# ============================================================

from core.cache import TTLCache

_CACHE_TTL = 55  # 缓存 55 秒（前端 60 秒刷新）
_mimo_cache = TTLCache(_CACHE_TTL)
_last_result: dict | None = None
_last_success_at: str | None = None
_last_error: str | None = None


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
    # MiMo 日界线是 UTC 0:00，直接用当前 UTC 日期
    now = datetime.utcnow()
    target_key = f"{now.month:02d}-{now.day:02d}"
    for t in tu:
        if t[0] == target_key:
            return max(0, t[1] - t[4])  # inTok - cache
    return 0


def _extract_today_tokens(daily_data: dict) -> dict:
    """从 daily_detail 提取今日 token 用量（MiMo 日界线 = UTC 0:00）。"""
    tu = daily_data.get("tokenUsage", [])
    now = datetime.utcnow()
    target_key = f"{now.month:02d}-{now.day:02d}"
    for t in tu:
        if t[0] == target_key:
            return {
                "in": t[1] or 0,
                "out": t[2] or 0,
                "total": t[3] or 0,
                "cache": t[4] or 0,
            }
    return {"in": 0, "out": 0, "total": 0, "cache": 0}


def _aggregate_nug_today_tokens() -> dict:
    """聚合 NUG 今日 token 用量（按 channel 汇总）。"""
    import providers.nug as _nug
    status = _nug.get_status()
    if not status.get("enabled"):
        return {"in": 0, "out": 0, "cache": 0, "total": 0}
    try:
        data = get_nug_channel_breakdown(days=1)
        rows = data.get("rows", [])
        in_tok = out_tok = cache_tok = total_tok = 0
        for r in rows:
            in_tok += r.get("inputTokens", 0)
            out_tok += r.get("outputTokens", 0)
            cache_tok += r.get("cacheReadTokens", 0)
            total_tok += r.get("totalTokens", 0)
        return {"in": in_tok, "out": out_tok, "cache": cache_tok, "total": total_tok}
    except Exception:
        return {"in": 0, "out": 0, "cache": 0, "total": 0}


def fetch_all_data() -> dict:
    """获取所有 MiMo + 本地平台数据（带缓存），供 dashboard 使用。"""
    global _last_result, _last_success_at, _last_error
    cached = _mimo_cache.get()
    if cached:
        return cached

    try:
        import providers.mimo as _mimo
        import providers.local_platform as _local

        from providers.mimo.api import get_mimo_api
        api = get_mimo_api()
        if api is None:
            _last_result = _mimo_expired_payload()
            _last_error = "MiMo Cookie unavailable or expired"
            return _last_result

        # 通过 providers 获取各项数据
        profile = _mimo.get_user_profile()
        plan = _mimo.get_plan_detail()
        usage = _mimo.get_plan_usage()
        balance_data = _mimo.get_balance()
        payg_usage = _mimo.get_usage_summary()
        tp_usage_detail = _mimo.get_model_breakdown()
        daily_data = _mimo.get_daily_detail()

        # 本地平台今日用量
        local_usage = _local.aggregate_today_usage()

        # NUG 今日 token 用量
        nug_today = _aggregate_nug_today_tokens()

        daily_data = daily_data or {}
        mimo_in_miss = _calculate_mimo_in_miss(daily_data)
        mimo_today = _extract_today_tokens(daily_data)

        # 合并 MiMo + 本地平台 + NUG 今日用量
        lu_in = (local_usage or {}).get("totalInputTokens", 0)
        lu_out = (local_usage or {}).get("totalOutputTokens", 0)
        lu_cache = (local_usage or {}).get("totalCacheReadTokens", 0)
        lu_total = (local_usage or {}).get("totalTokens", 0)
        today = {
            "in": mimo_today["in"] + lu_in + nug_today["in"],
            "out": mimo_today["out"] + lu_out + nug_today["out"],
            "cache": mimo_today["cache"] + lu_cache + nug_today["cache"],
            "total": mimo_today["total"] + lu_total + nug_today["total"],
            "inMiss": mimo_in_miss + lu_in + nug_today["in"],
        }

        # 转换 balance 为前端期望的格式
        balance_payload = {}
        if balance_data:
            balance_payload = {
                "balance": balance_data.get("balance", "0"),
                "currency": balance_data.get("currency", "CNY"),
                **balance_data.get("details", {}),
            }

        result = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "profile": profile or {},
            "plan": plan or {},
            "usage": usage or {},
            "balance": balance_payload,
            "payg_usage": payg_usage or {},
            "tp_usage_detail": tp_usage_detail or [],
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


def get_nug_payload() -> dict:
    """NUG 余额数据，供 dashboard 使用。"""
    import providers.nug as _nug
    status = _nug.get_status()
    if not status.get("enabled"):
        return {"enabled": False}
    balance = _nug.get_balance()
    if balance is None:
        return {"enabled": True, "error": "获取数据失败"}
    return {"enabled": True, "balance": float(balance.get("balance", 0))}


def get_nug_channel_breakdown(days: int = 7) -> dict:
    """NUG 按 channel 分组用量，供 dashboard 使用。"""
    import providers.nug as _nug
    status = _nug.get_status()
    if not status.get("enabled"):
        return {"enabled": False, "rows": []}
    rows = _nug.get_channel_breakdown(days=days)
    return {"enabled": True, "rows": rows or []}
