"""NFK provider — LocalMimoAPI 客户端。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import warnings

import requests
from urllib.parse import urlparse
from urllib3.exceptions import InsecureRequestWarning

from providers.nfk.token_cache import load_cached_token, save_cached_token

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logger = logging.getLogger("cuckoo.providers.nfk")


def _is_private_host(url: str) -> bool:
    """判断 URL 的主机是否为私网/本机地址（RFC 1918 + loopback）。"""
    host = urlparse(url).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


class LocalMimoAPI:
    """NFK API 客户端（JWT 认证，token 持久化）"""

    def __init__(self, base_url: str, username: str, password: str, *, account_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.account_id = account_id or ""
        self._token = ""
        self._token_ts: float = 0
        # 仅对私网地址的 HTTPS 自签证书跳过验证；必须解析 hostname 精确匹配，
        # 简单子串匹配会误伤任何 URL 中含 "10." / "172." 的公网主机。
        self._verify = not (
            self.base_url.startswith("https://") and _is_private_host(self.base_url)
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
            logger.error(f"[nfk] 登录失败 {self.base_url}: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[nfk] 登录异常 {self.base_url}: {e}")
        return False

    def _ensure_token(self) -> bool:
        """确保 token 有效（5天刷新，磁盘缓存）。"""
        if self._token and (time.time() - self._token_ts) < 5 * 86400:
            return True
        cached = load_cached_token(self._cache_key, self.account_id)
        if cached:
            # 沿用 Vault 中的原始签发时间，内存 TTL 不允许比 Vault TTL 更宽。
            self._token, self._token_ts = cached
            return True
        return self._login()

    def _invalidate_token(self) -> None:
        self._token = ""
        self._token_ts = 0.0

    def _get_timeseries(self) -> requests.Response:
        return requests.get(
            f"{self.base_url}/api/usage-history/timeseries",
            params={"granularity": "day"},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10, verify=self._verify,
        )

    def get_today_usage(self) -> dict | None:
        """获取今日使用量（timeseries 取今天的点）。"""
        if not self._ensure_token():
            return None
        try:
            resp = self._get_timeseries()
            if resp.status_code in (401, 403):
                # 缓存 JWT 已被服务端拒绝（过期/改密）；不重登的话，最长 5 天内
                # _ensure_token 都会拿着这个死 token 返回 True，Provider 静默无数据。
                logger.info(f"[nfk] JWT 被拒绝，重新登录 {self.base_url}")
                self._invalidate_token()
                if not self._login():
                    return None
                resp = self._get_timeseries()
            if resp.status_code != 200:
                logger.error(f"[nfk] 获取数据失败 {self.base_url}: HTTP {resp.status_code}")
                return None
            data = resp.json()
            points = data.get("points", [])
            if not points:
                return None
            # NFK 时间戳是 UTC，MiMo 日界线也是 UTC 0:00，直接用当前 UTC 日期
            target_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for p in points:
                ts = p.get("timestamp", "")
                if ts.startswith(target_str) and (p.get("requestCount") or 0) > 0:
                    return p
            return None
        except Exception as e:
            logger.error(f"[nfk] 获取数据异常 {self.base_url}: {e}")
            return None
