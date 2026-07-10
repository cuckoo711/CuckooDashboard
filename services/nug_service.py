"""NUG platform balance service."""

from __future__ import annotations

import logging

from datetime import datetime, timezone

import requests

from services.config import load_config

logger = logging.getLogger("cuckoo.nug")


class NUGApi:
    """NUG (NarraFork) platform API client using session cookie auth."""

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
        """Log in and obtain a session cookie."""
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

    def get_channel_breakdown(self, days: int = 7) -> list | None:
        """获取按 channel 分组的用量明细（最近 N 天）"""
        if not self._logged_in:
            if not self._login():
                return None
        try:
            from datetime import datetime, timedelta, timezone
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            params = {
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "groupBy": "channel",
                "limit": 100,
            }
            resp = self.session.get(
                f"{self.base_url}/api/user/stats-explorer/breakdown",
                params=params,
                timeout=15,
            )
            if resp.status_code == 401:
                if self._login():
                    resp = self.session.get(
                        f"{self.base_url}/api/user/stats-explorer/breakdown",
                        params=params,
                        timeout=15,
                    )
                else:
                    return None
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get("rows", [])
        except Exception as e:
            logger.error(f"[nug] 获取 channel breakdown 异常: {e}")
            return None

    def get_data(self) -> dict | None:
        """Fetch current balance data."""
        if not self._logged_in:
            if not self._login():
                return None
        try:
            me_resp = self.session.get(f"{self.base_url}/api/auth/me", timeout=10)
            if me_resp.status_code == 401:
                if self._login():
                    me_resp = self.session.get(f"{self.base_url}/api/auth/me", timeout=10)
                else:
                    return None
            if me_resp.status_code != 200:
                logger.error(f"[nug] 获取用户信息失败 {self.base_url}: HTTP {me_resp.status_code}")
                return None
            me = me_resp.json()
            return {"balance": me.get("quotaBalance", 0)}
        except Exception as e:
            logger.error(f"[nug] 获取数据异常 {self.base_url}: {e}")
            return None


_nug_api: NUGApi | None = None
_last_payload: dict | None = None
_last_success_at: str | None = None
_last_error: str | None = None


def get_nug_api() -> NUGApi | None:
    """Return the configured singleton NUG API client."""
    global _nug_api
    if _nug_api is not None:
        return _nug_api
    config = load_config()
    nug = config.get("nug", {})
    if not nug.get("enabled"):
        return None
    url = nug.get("url", "")
    username = nug.get("username", "")
    password = nug.get("password", "")
    if not all([url, username, password]):
        return None
    _nug_api = NUGApi(url, username, password)
    return _nug_api


def get_nug_payload() -> dict:
    """Return the dashboard-compatible NUG API payload."""
    global _last_payload, _last_success_at, _last_error
    nug = get_nug_api()
    if not nug:
        _last_payload = {"enabled": False}
        _last_error = None
        return _last_payload
    data = nug.get_data()
    if data is None:
        _last_error = "获取数据失败"
        _last_payload = {"enabled": True, "error": _last_error}
        return _last_payload
    _last_error = None
    _last_success_at = datetime.now(timezone.utc).isoformat()
    _last_payload = {"enabled": True, **data}
    return _last_payload


def get_nug_status() -> dict:
    """Return NUG status without logging in or contacting the remote API."""
    nug_config = load_config().get("nug", {})
    enabled = bool(nug_config.get("enabled"))
    if not enabled:
        status = "disabled"
    elif _last_error:
        status = "error"
    elif _last_payload and "error" not in _last_payload:
        status = "ok"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "stale": False,
        "error": _last_error,
        "last_success_at": _last_success_at,
        "details": {},
    }
