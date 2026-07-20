"""NFK Provider：JWT token Vault 缓存。"""

from __future__ import annotations

import time
from typing import Any

from core.credentials import get_provider_state, update_provider_state

_PROVIDER_ID = "nfk"
_TOKEN_TTL_SECONDS = 5 * 86400


def _normalise_state(value: object) -> dict[str, Any]:
    state = dict(value) if isinstance(value, dict) else {}
    state["accounts"] = dict(state.get("accounts") or {})
    active = state.get("active_account_id")
    state["active_account_id"] = active if isinstance(active, str) else ""
    return state


def load_cached_token(cache_key: str, account_id: str | None = None) -> str | None:
    """读取账户下指定 URL 的 JWT，保持旧版 5 天有效期语义。"""
    state = _normalise_state(get_provider_state(_PROVIDER_ID, {}))
    resolved = account_id or state["active_account_id"]
    account = state["accounts"].get(resolved)
    if not isinstance(account, dict):
        return None
    tokens = account.get("tokens")
    if not isinstance(tokens, dict):
        return None
    entry = tokens.get(cache_key)
    if not isinstance(entry, dict) or not entry.get("token"):
        return None
    if (time.time() - float(entry.get("ts") or 0)) >= _TOKEN_TTL_SECONDS:
        return None
    return str(entry["token"])


def save_cached_token(cache_key: str, token: str, account_id: str | None = None) -> None:
    """将 JWT 写入引用账户的 DPAPI Vault state。"""
    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        resolved = account_id or state["active_account_id"]
        account = state["accounts"].get(resolved)
        if not isinstance(account, dict):
            return state
        tokens = dict(account.get("tokens") or {})
        tokens[cache_key] = {"token": token, "ts": time.time()}
        account["tokens"] = tokens
        state["accounts"][resolved] = account
        return state

    update_provider_state(_PROVIDER_ID, apply)
