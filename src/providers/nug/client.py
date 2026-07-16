"""NUG Provider 的会话客户端；session cookie 由 Provider Vault state 持久化。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger("cuckoo.providers.nug")


class NUGClient:
    """NUG API 客户端，支持从 Vault 恢复并回写 session cookie。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        session_cookies: Mapping[str, str] | None = None,
        on_session_update: Callable[[dict[str, str]], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Dashboard",
            "Content-Type": "application/json",
        })
        if isinstance(session_cookies, Mapping):
            self.session.cookies.update({str(key): str(value) for key, value in session_cookies.items() if value})
        self._on_session_update = on_session_update
        self._logged_in = bool(session_cookies)

    def _persist_session(self) -> None:
        if self._on_session_update is None:
            return
        try:
            self._on_session_update(dict(self.session.cookies.get_dict()))
        except Exception:
            logger.warning("[nug] 无法持久化会话状态")

    def _login(self) -> bool:
        try:
            resp = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=10,
            )
            if resp.status_code == 200:
                self._logged_in = True
                self._persist_session()
                logger.info("[nug] 登录成功: %s", self.base_url)
                return True
            logger.error("[nug] 登录失败 %s: HTTP %s", self.base_url, resp.status_code)
        except Exception as exc:
            logger.error("[nug] 登录异常 %s: %s", self.base_url, exc)
        return False

    def _ensure_login(self) -> bool:
        if not self._logged_in:
            return self._login()
        return True

    def _request_with_retry(self, method: str, url: str, **kwargs: Any):
        """带 401 自动重登的请求；成功请求后回写服务端可能轮换的 session。"""
        if not self._ensure_login():
            return None
        try:
            resp = getattr(self.session, method)(url, **kwargs)
        except Exception as exc:
            logger.error("[nug] 请求异常: %s", exc)
            return None
        if resp.status_code == 401:
            self._logged_in = False
            if self._login():
                try:
                    resp = getattr(self.session, method)(url, **kwargs)
                except Exception as exc:
                    logger.error("[nug] 重登后请求异常: %s", exc)
                    return None
            else:
                return None
        if resp.status_code == 200:
            self._persist_session()
            return resp
        return None

    def get_balance(self) -> dict | None:
        try:
            resp = self._request_with_retry("get", f"{self.base_url}/api/auth/me", timeout=10)
            if resp is None:
                return None
            me = resp.json()
            return {"balance": me.get("quotaBalance", 0)}
        except Exception as exc:
            logger.error("[nug] 获取余额异常: %s", exc)
            return None

    def get_channel_breakdown(self, days: int = 7) -> list | None:
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
            return resp.json().get("rows", [])
        except Exception as exc:
            logger.error("[nug] 获取 channel breakdown 异常: %s", exc)
            return None
