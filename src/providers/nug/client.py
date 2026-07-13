"""NUG (NarraFork) 平台 provider — 会话管理与 API 客户端。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger("cuckoo.providers.nug")


class NUGClient:
    """NUG 平台 API 客户端，使用 session cookie 认证。"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Dashboard",
            "Content-Type": "application/json",
        })
        self._logged_in = False

    def _login(self) -> bool:
        """登录获取 session cookie。"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=10,
            )
            if resp.status_code == 200:
                self._logged_in = True
                logger.info(f"[nug] 登录成功: {self.base_url}")
                return True
            logger.error(f"[nug] 登录失败 {self.base_url}: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[nug] 登录异常 {self.base_url}: {e}")
        return False

    def _ensure_login(self) -> bool:
        if not self._logged_in:
            return self._login()
        return True

    def _request_with_retry(self, method: str, url: str, **kwargs):
        """带 401 自动重登的请求。"""
        if not self._ensure_login():
            return None
        resp = getattr(self.session, method)(url, **kwargs)
        if resp.status_code == 401:
            if self._login():
                resp = getattr(self.session, method)(url, **kwargs)
            else:
                return None
        return resp if resp.status_code == 200 else None

    def get_balance(self) -> dict | None:
        """获取 quotaBalance。"""
        try:
            resp = self._request_with_retry(
                "get", f"{self.base_url}/api/auth/me", timeout=10
            )
            if resp is None:
                return None
            me = resp.json()
            return {"balance": me.get("quotaBalance", 0)}
        except Exception as e:
            logger.error(f"[nug] 获取余额异常: {e}")
            return None

    def get_channel_breakdown(self, days: int = 7) -> list | None:
        """获取按 channel 分组的用量明细（最近 N 天）。"""
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            params = {
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "groupBy": "channel",
                "limit": 100,
            }
            resp = self._request_with_retry(
                "get",
                f"{self.base_url}/api/user/stats-explorer/breakdown",
                params=params,
                timeout=15,
            )
            if resp is None:
                return None
            data = resp.json()
            return data.get("rows", [])
        except Exception as e:
            logger.error(f"[nug] 获取 channel breakdown 异常: {e}")
            return None
