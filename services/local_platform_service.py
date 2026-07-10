"""Local MiMo-compatible platform clients and usage aggregation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from services.config import load_config

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_TOKEN_CACHE = BASE_DIR / "local_tokens.json"


class LocalMimoAPI:
    """本地 MiMo 平台 API 客户端（JWT 认证，token 持久化到磁盘）"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token = ""
        self._token_ts = 0  # token 获取时间
        # 内网 HTTPS 自签证书跳过验证
        self._verify = not (
            self.base_url.startswith("https://") and any(
                host in self.base_url for host in ["192.168.", "10.", "172."]
            )
        )

    @property
    def _cache_key(self) -> str:
        return self.base_url

    def _load_cached_token(self) -> bool:
        """从磁盘缓存恢复 token"""
        if not LOCAL_TOKEN_CACHE.exists():
            return False
        try:
            cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
            entry = cache.get(self._cache_key)
            if entry and entry.get("token"):
                self._token = entry["token"]
                self._token_ts = entry.get("ts", 0)
                # 检查是否还在有效期内（5天）
                if (time.time() - self._token_ts) < 5 * 86400:
                    return True
                self._token = ""
                self._token_ts = 0
        except (json.JSONDecodeError, OSError):
            pass
        return False

    def _save_cached_token(self):
        """将 token 持久化到磁盘"""
        cache = {}
        if LOCAL_TOKEN_CACHE.exists():
            try:
                cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cache = {}
        cache[self._cache_key] = {"token": self._token, "ts": self._token_ts}
        try:
            LOCAL_TOKEN_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _login(self) -> bool:
        """登录获取 JWT Token"""
        try:
            resp = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=8, verify=self._verify,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("token", "")
                self._token_ts = time.time()
                if self._token:
                    self._save_cached_token()
                    return True
            print(f"[local] 登录失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[local] 登录异常 {self.base_url}: {e}", flush=True)
        return False

    def _ensure_token(self) -> bool:
        """确保 token 有效（5天刷新，磁盘缓存）"""
        if self._token and (time.time() - self._token_ts) < 5 * 86400:
            return True
        if self._load_cached_token():
            return True
        return self._login()

    def get_today_usage(self) -> dict | None:
        """获取今日使用量（timeseries 取今天的点）"""
        if not self._ensure_token():
            return None
        try:
            resp = requests.get(
                f"{self.base_url}/api/usage-history/timeseries",
                params={"granularity": "day"},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10, verify=self._verify,
            )
            if resp.status_code != 200:
                print(f"[local] 获取数据失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
                return None
            data = resp.json()
            points = data.get("points", [])
            if not points:
                return None
            # MiMo 按 UTC 0 点重置 = 北京时间 8:00
            # 早 8 点前（北京时间）用昨天 UTC 日期，8 点后用今天
            bj_now = datetime.now(timezone(timedelta(hours=8)))
            ref = datetime.utcnow() if bj_now.hour >= 8 else datetime.utcnow() - timedelta(days=1)
            target_str = ref.strftime("%Y-%m-%d")
            for p in points:
                ts = p.get("timestamp", "")
                if ts.startswith(target_str) and (p.get("requestCount") or 0) > 0:
                    return p
            return None
        except Exception as e:
            print(f"[local] 获取数据异常 {self.base_url}: {e}", flush=True)
            return None


_local_apis: list[LocalMimoAPI] | None = None


def get_local_apis() -> list[LocalMimoAPI]:
    """获取所有已配置且可达的本地平台 API 实例（单例，启动时探测）"""
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
    print(f"[local] 已配置 {len(_local_apis)} 个本地平台", flush=True)
    return _local_apis


def empty_local_usage() -> dict:
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


def aggregate_local_usage() -> dict | None:
    """获取所有本地平台今日使用量并聚合；无可用数据时返回 None。"""
    local_usage = empty_local_usage()
    has_local = False
    for api in get_local_apis():
        today = api.get_today_usage()
        if today:
            has_local = True
            local_usage["requestCount"] += today.get("requestCount", 0)
            # 本地平台: totalTokens = in + out + cacheRead
            local_usage["totalInputTokens"] += today.get("totalInputTokens", 0)
            local_usage["totalOutputTokens"] += today.get("totalOutputTokens", 0)
            local_usage["totalCacheReadTokens"] += today.get("totalCacheReadTokens", 0)
            local_usage["totalTokens"] += today.get("totalTokens", 0)
            local_usage["totalReasoningTokens"] += today.get("totalReasoningTokens", 0)
            local_usage["totalCost"] += today.get("totalCost", 0)
            local_usage["errorCount"] += today.get("errorCount", 0)
            local_usage["meterUsage"] += today.get("meterUsage", 0)
    return local_usage if has_local else None
