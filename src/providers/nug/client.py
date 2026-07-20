"""NUG Provider 的会话客户端；session cookie 由 Provider Vault state 持久化。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger("cuckoo.providers.nug")

# 仅这些状态码说明会话失效，值得重登重试；5xx/429 重登无济于事。
_AUTH_RETRY_STATUS = frozenset({401, 403})


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
        self._persisted_cookies: dict[str, str] = dict(self.session.cookies.get_dict())

    def _persist_session(self) -> None:
        if self._on_session_update is None:
            return
        cookies = dict(self.session.cookies.get_dict())
        if cookies == self._persisted_cookies:
            return
        try:
            self._on_session_update(cookies)
            self._persisted_cookies = cookies
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

    def _do_request(self, method: str, url: str, **kwargs: Any):
        """执行一次请求，返回 response 或 None。"""
        try:
            return getattr(self.session, method)(url, **kwargs)
        except Exception as exc:
            logger.error("[nug] 请求异常: %s", exc)
            return None

    def _request_with_retry(self, method: str, url: str, **kwargs: Any):
        """带自动重登的请求；仅会话失效（401/403）时重登一次再重试。"""
        if not self._ensure_login():
            return None
        resp = self._do_request(method, url, **kwargs)
        if resp is None:
            return None
        if resp.status_code == 200:
            self._persist_session()
            return resp
        if resp.status_code not in _AUTH_RETRY_STATUS:
            logger.warning("[nug] 请求 %s 返回 %s", url, resp.status_code)
            return None
        logger.warning("[nug] 请求 %s 返回 %s，会话可能已失效，尝试重登", url, resp.status_code)
        self._logged_in = False
        if not self._login():
            return None
        resp = self._do_request(method, url, **kwargs)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.error("[nug] 重登后请求 %s 仍返回 %s", url, resp.status_code)
            return None
        self._persist_session()
        return resp

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

    def get_channel_breakdown(
        self,
        days: int = 7,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list | None:
        try:
            end = end or datetime.now(timezone.utc)
            start = start or end - timedelta(days=days)
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
