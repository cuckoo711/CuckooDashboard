"""将 v2 明文认证数据一次性迁移到 DPAPI Vault。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from core.credentials import vault

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_MIMO_COOKIES = PROJECT_ROOT / "config" / "cookies.json"
LEGACY_LOCAL_TOKENS = PROJECT_ROOT / "data" / "local_tokens.json"


def _identifier(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _load_json(path: Path) -> tuple[Any, bool]:
    if not path.exists():
        return None, False
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (OSError, json.JSONDecodeError):
        # 损坏的来源绝不能删除；用户需要自行重新认证或排查。
        return None, False


def _provider_state(root: dict[str, Any], provider_id: str) -> dict[str, Any]:
    providers = root.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        root["providers"] = providers
    raw = providers.get(provider_id)
    state = dict(raw) if isinstance(raw, dict) else {}
    state["accounts"] = dict(state.get("accounts") or {})
    active = state.get("active_account_id")
    state["active_account_id"] = active if isinstance(active, str) else ""
    providers[provider_id] = state
    return state


def _put_account(state: dict[str, Any], account_id: str, data: dict[str, Any]) -> str:
    accounts = state["accounts"]
    existing = accounts.get(account_id)
    if not isinstance(existing, dict):
        accounts[account_id] = copy.deepcopy(data)
    if not state.get("active_account_id"):
        state["active_account_id"] = account_id
    return account_id


def migrate_legacy_credentials(config: dict[str, Any]) -> tuple[dict[str, Any], bool, list[Path]]:
    """迁移已知明文凭据并返回 ``(sanitized_config, changed, cleanup_paths)``。

    调用方必须先成功持久化返回的 YAML，才能删除 ``cleanup_paths``。Vault 已有的
    值/账户优先于旧来源，保证重试和中断后不会覆盖已更新的认证状态。
    """
    next_config = copy.deepcopy(config)
    dashboard = _mapping(next_config.get("dashboard"))
    providers_config = _mapping(next_config.get("providers"))
    next_config["dashboard"] = dashboard
    next_config["providers"] = providers_config

    cookie_data, cookie_valid = _load_json(LEGACY_MIMO_COOKIES)
    token_data, token_valid = _load_json(LEGACY_LOCAL_TOKENS)
    source_has_data = bool(
        dashboard.get("token")
        or next_config.get("github_token")
        or cookie_valid
        or token_valid
        or _mapping(providers_config.get("nug")).get("password")
        or _mapping(providers_config.get("local_platform")).get("password")
        or _mapping(providers_config.get("local_platform")).get("username")
    )

    if not source_has_data:
        return next_config, False, []

    local_config = _mapping(providers_config.get("local_platform"))
    nug_config = _mapping(providers_config.get("nug"))
    changed = False

    def apply(root: dict[str, Any]) -> None:
        global_values = root.setdefault("global", {})
        if not isinstance(global_values, dict):
            global_values = {}
            root["global"] = global_values

        dashboard_token = dashboard.get("token")
        if isinstance(dashboard_token, str) and dashboard_token and not global_values.get("dashboard_token"):
            global_values["dashboard_token"] = dashboard_token
        github_token = next_config.get("github_token")
        if isinstance(github_token, str) and github_token and not global_values.get("github_token"):
            global_values["github_token"] = github_token

        # MiMo：保留旧 Cookie JSON 中的所有未知字段，避免丢失 passToken 等刷新数据。
        if isinstance(cookie_data, dict) and cookie_data.get("cookie"):
            mimo_state = _provider_state(root, "mimo")
            user_id = str(cookie_data.get("userId") or "")
            account_id = f"mimo-{user_id}" if user_id else "mimo-migrated"
            _put_account(mimo_state, account_id, {
                **copy.deepcopy(cookie_data),
                "label": str(cookie_data.get("label") or user_id or "迁移的 MiMo 账户"),
            })

        # NUG：v2 在 YAML 中保存完整单账户信息，v3 转换为 Vault 账户。
        nug_url = str(nug_config.get("url") or "").strip().rstrip("/")
        nug_username = str(nug_config.get("username") or "").strip()
        nug_password = str(nug_config.get("password") or "")
        if nug_url and nug_username and nug_password:
            nug_state = _provider_state(root, "nug")
            account_id = _identifier("nug", f"{nug_url}\n{nug_username}")
            _put_account(nug_state, account_id, {
                "label": nug_username,
                "url": nug_url,
                "username": nug_username,
                "password": nug_password,
                "session_cookies": {},
            })

        # Local Platform：URL 保留在 YAML，密码/账户/JWT 转进 Vault；每个 URL 都显式指向账户。
        local_state = _provider_state(root, "local_platform")
        local_username = str(local_config.get("username") or "").strip()
        local_password = str(local_config.get("password") or "")
        default_account_id = ""
        if local_username and local_password:
            default_account_id = _identifier("local", f"{local_username}\ndefault")
            _put_account(local_state, default_account_id, {
                "label": local_username,
                "username": local_username,
                "password": local_password,
                "tokens": {},
            })
        elif isinstance(local_state.get("active_account_id"), str):
            default_account_id = local_state["active_account_id"]

        raw_urls = local_config.get("urls")
        rebuilt_urls: list[dict[str, str]] = []
        for raw in raw_urls if isinstance(raw_urls, list) else []:
            item = {"url": raw} if isinstance(raw, str) else _mapping(raw)
            url = str(item.get("url") or "").strip().rstrip("/")
            if not url:
                continue
            password_override = str(item.get("password") or "")
            ref = str(item.get("credential_ref") or "")
            if not ref and password_override and local_username:
                ref = _identifier("local", f"{local_username}\n{url}")
                _put_account(local_state, ref, {
                    "label": f"{local_username} · {url}",
                    "username": local_username,
                    "password": password_override,
                    "tokens": {},
                })
            if not ref:
                ref = default_account_id
            rebuilt_urls.append({"url": url, "credential_ref": ref})

        # 旧 JWT 依据 URL 写入 URL 所引用的账户；没有可用账户时创建 token-only 账户，
        # 让当前 token 仍可使用，过期后认证页会要求补齐账号密码。
        if isinstance(token_data, dict):
            refs_by_url = {row["url"]: row.get("credential_ref", "") for row in rebuilt_urls}
            for raw_url, entry in token_data.items():
                if not isinstance(entry, dict) or not entry.get("token"):
                    continue
                url = str(raw_url).rstrip("/")
                ref = refs_by_url.get(url) or default_account_id
                if not ref:
                    ref = _identifier("local-token", url)
                    _put_account(local_state, ref, {
                        "label": f"迁移的 JWT · {url}",
                        "username": "",
                        "password": "",
                        "tokens": {},
                    })
                    rebuilt_urls.append({"url": url, "credential_ref": ref})
                account = local_state["accounts"].get(ref)
                if isinstance(account, dict):
                    tokens = dict(account.get("tokens") or {})
                    tokens.setdefault(url, {"token": str(entry["token"]), "ts": entry.get("ts", 0)})
                    account["tokens"] = tokens
                    local_state["accounts"][ref] = account

        if rebuilt_urls:
            local_config["urls"] = rebuilt_urls
            providers_config["local_platform"] = local_config

    # Vault 更新完成前绝不移除明文来源。
    vault.update(apply)

    # Vault 已安全落盘后，移除 v2 YAML 的全部秘密/账户字段。
    if "token" in dashboard:
        dashboard.pop("token", None)
        changed = True
    if "github_token" in next_config:
        next_config.pop("github_token", None)
        changed = True

    if nug_config:
        for key in ("url", "username", "password"):
            if key in nug_config:
                nug_config.pop(key, None)
                changed = True
        providers_config["nug"] = nug_config

    if local_config:
        for key in ("username", "password"):
            if key in local_config:
                local_config.pop(key, None)
                changed = True
        raw_urls = local_config.get("urls")
        normalized_urls: list[dict[str, str]] = []
        for raw in raw_urls if isinstance(raw_urls, list) else []:
            item = {"url": raw} if isinstance(raw, str) else _mapping(raw)
            url = str(item.get("url") or "").strip().rstrip("/")
            if not url:
                continue
            normalized_urls.append({
                "url": url,
                "credential_ref": str(item.get("credential_ref") or ""),
            })
        if raw_urls != normalized_urls:
            local_config["urls"] = normalized_urls
            changed = True
        providers_config["local_platform"] = local_config

    cleanup: list[Path] = []
    if cookie_valid:
        cleanup.append(LEGACY_MIMO_COOKIES)
    if token_valid:
        cleanup.append(LEGACY_LOCAL_TOKENS)
    return next_config, changed, cleanup
