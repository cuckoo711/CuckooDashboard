"""NUG platform balance service."""

from __future__ import annotations

import requests

from services.config import load_config


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
                print(f"[nug] 登录成功: {self.base_url}", flush=True)
                return True
            print(f"[nug] 登录失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[nug] 登录异常 {self.base_url}: {e}", flush=True)
        return False

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
                print(f"[nug] 获取用户信息失败 {self.base_url}: HTTP {me_resp.status_code}", flush=True)
                return None
            me = me_resp.json()
            return {"balance": me.get("quotaBalance", 0)}
        except Exception as e:
            print(f"[nug] 获取数据异常 {self.base_url}: {e}", flush=True)
            return None


_nug_api: NUGApi | None = None


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
    nug = get_nug_api()
    if not nug:
        return {"enabled": False}
    data = nug.get_data()
    if data is None:
        return {"enabled": True, "error": "获取数据失败"}
    return {"enabled": True, **data}
