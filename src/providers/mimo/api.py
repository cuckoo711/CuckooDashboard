"""MiMo 官方平台 provider — API 封装与 Cookie 管理。

从 mimo_usage.py 导入 MiMoAPI 及 Cookie 工具函数，
提供自动检测有效性、过期刷新功能。
"""

from __future__ import annotations

import logging
import time

from core.config import get_provider_config

try:
    from mimo_usage import MiMoAPI, load_cookies, refresh_mimo_cookie, save_cookies
except ImportError as exc:
    raise RuntimeError("无法导入 mimo_usage.py，请确保文件存在") from exc

logger = logging.getLogger("cuckoo.providers.mimo")

_mimo_cookie_valid: bool | None = None  # None=未检测, True=有效, False=过期
_mimo_cookie_last_check: float = 0  # 上次检测时间戳


def get_mimo_api() -> MiMoAPI | None:
    """获取 MiMoAPI 实例，自动检测 Cookie 有效性。

    过期时尝试用 passToken 自动刷新，刷新失败返回 None。
    """
    global _mimo_cookie_valid, _mimo_cookie_last_check
    mimo_config = get_provider_config("mimo", {})
    if isinstance(mimo_config, dict) and not mimo_config.get("enabled", True):
        return None
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
            logger.error("[MiMo] Cookie 已过期，尝试自动刷新...")
            new_cookie = refresh_mimo_cookie(cookie_str)
            if new_cookie:
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


def reload_config() -> None:
    """清理 MiMo Cookie 有效性检测缓存。"""
    global _mimo_cookie_valid, _mimo_cookie_last_check
    _mimo_cookie_valid = None
    _mimo_cookie_last_check = 0


def is_cookie_valid() -> bool | None:
    """返回当前 Cookie 有效状态（不触发网络请求）。"""
    return _mimo_cookie_valid
