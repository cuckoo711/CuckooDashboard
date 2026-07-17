"""本地平台 provider — LocalMimoAPI 客户端。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from providers.local_platform.token_cache import load_cached_token, save_cached_token

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logger = logging.getLogger("cuckoo.providers.local_platform")


class LocalMimoAPI:
    """本地 MiMo 兼容平台 API 客户端（JWT 认证，token 持久化）"""

    def __init__(self, base_url: str, username: str, password: str, *, account_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.account_id = account_id or ""
        self._token = ""
        self._token_ts: float = 0
        # 内网 HTTPS 自签证书跳过验证
        self._verify = not (
            self.base_url.startswith("https://") and any(
                host in self.base_url for host in ["192.168.", "10.", "172."]
            )
        )

    @property
    def _cache_key(self) -> str:
        return self.base_url

    def _login(self) -> bool:
        """登录获取 JWT Token。"""
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
                    save_cached_token(self._cache_key, self._token, self.account_id)
                    return True
            logger.error(f"[local] 登录失败 {self.base_url}: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[local] 登录异常 {self.base_url}: {e}")
        return False

    def _ensure_token(self) -> bool:
        """确保 token 有效（5天刷新，磁盘缓存）。"""
        if self._token and (time.time() - self._token_ts) < 5 * 86400:
            return True
        cached = load_cached_token(self._cache_key, self.account_id)
        if cached:
            self._token = cached
            self._token_ts = time.time()
            return True
        return self._login()

    def get_today_usage(self) -> dict | None:
        """获取今日使用量（timeseries 取今天的点）。"""
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
                logger.error(f"[local] 获取数据失败 {self.base_url}: HTTP {resp.status_code}")
                return None
            data = resp.json()
            points = data.get("points", [])
            if not points:
                return None
            # 本地平台时间戳是 UTC，MiMo 日界线也是 UTC 0:00，直接用当前 UTC 日期
            target_str = datetime.utcnow().strftime("%Y-%m-%d")
            for p in points:
                ts = p.get("timestamp", "")
                if ts.startswith(target_str) and (p.get("requestCount") or 0) > 0:
                    return p
            return None
        except Exception as e:
            logger.error(f"[local] 获取数据异常 {self.base_url}: {e}")
            return None
