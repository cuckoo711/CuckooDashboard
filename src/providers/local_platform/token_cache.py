"""本地平台 provider — JWT token 磁盘缓存。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import DATA_DIR

LOCAL_TOKEN_CACHE = DATA_DIR / "local_tokens.json"


def load_cached_token(cache_key: str) -> str | None:
    """从磁盘缓存加载 token，有效期 5 天。"""
    if not LOCAL_TOKEN_CACHE.exists():
        return None
    try:
        cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
        entry = cache.get(cache_key)
        if entry and entry.get("token"):
            if (time.time() - entry.get("ts", 0)) < 5 * 86400:
                return entry["token"]
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_cached_token(cache_key: str, token: str) -> None:
    """将 token 持久化到磁盘。"""
    cache = {}
    if LOCAL_TOKEN_CACHE.exists():
        try:
            cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}
    cache[cache_key] = {"token": token, "ts": time.time()}
    try:
        LOCAL_TOKEN_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass
