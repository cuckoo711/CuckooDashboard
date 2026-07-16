"""NUG Provider：数据能力、多账户 Vault 和自定义认证页面。"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core.config import get_provider_config
from core.credentials import VaultError, get_provider_state, update_provider_state
from providers.auth import RefreshResult, auto_refresh, get_refresh_status
from providers.nug.client import NUGClient

logger = logging.getLogger("cuckoo.providers.nug")

CAPABILITIES = ["balance", "api_usage"]

CONFIG_SCHEMA = {
    "config_key": "nug",
    "title": "NUG",
    "description": "NarraFork 平台余额和渠道用量。账户与会话由 Windows DPAPI 凭据 Vault 管理。",
    "order": 30,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": False},
    ],
    "status_only_auth": True,
}

AUTH_DESCRIPTOR = {
    "title": "NUG 账户认证",
    "auth_path": "/auth/nug/",
    "custom_ui": True,
}

_CLIENT: NUGClient | None = None
_CLIENT_ACCOUNT_ID = ""
_LAST_SUCCESS_AT: str | None = None
_LAST_ERROR: str | None = None


def _normalise_state(value: object) -> dict[str, Any]:
    state = dict(value) if isinstance(value, dict) else {}
    state["accounts"] = dict(state.get("accounts") or {})
    active = state.get("active_account_id")
    state["active_account_id"] = active if isinstance(active, str) else ""
    return state


def _state() -> dict[str, Any]:
    return _normalise_state(get_provider_state("nug", {}))


def _enabled() -> bool:
    config = get_provider_config("nug", {})
    return bool(config.get("enabled")) if isinstance(config, dict) else False


def get_nug_account(account_id: str | None = None) -> dict[str, Any]:
    state = _state()
    resolved = account_id or state["active_account_id"]
    account = state["accounts"].get(resolved)
    if not isinstance(account, dict):
        return {}
    result = dict(account)
    result["_account_id"] = resolved
    return result


def list_accounts() -> list[dict[str, Any]]:
    try:
        state = _state()
    except VaultError:
        return []
    active = state["active_account_id"]
    result: list[dict[str, Any]] = []
    for account_id, account in state["accounts"].items():
        if not isinstance(account, dict):
            continue
        result.append({
            "id": account_id,
            "label": str(account.get("label") or account.get("username") or account_id),
            "url": str(account.get("url") or ""),
            "username": str(account.get("username") or ""),
            "configured": bool(account.get("password")),
            "active": account_id == active,
            "last_success_at": account.get("last_success_at"),
        })
    return sorted(result, key=lambda item: (not item["active"], item["label"].casefold(), item["id"]))


def save_account(payload: dict[str, Any], account_id: str | None = None) -> str:
    url = str(payload.get("url") or "").strip().rstrip("/")
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    label = str(payload.get("label") or "").strip()
    if not url or not username or not password:
        raise ValueError("服务地址、用户名和密码不能为空")
    resolved = account_id or f"nug-{uuid.uuid4().hex[:12]}"

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        previous = state["accounts"].get(resolved)
        account = dict(previous) if isinstance(previous, dict) else {}
        credentials_changed = (
            account.get("url") != url
            or account.get("username") != username
            or account.get("password") != password
        )
        account.update({
            "label": label or str(account.get("label") or username),
            "url": url,
            "username": username,
            "password": password,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        if credentials_changed:
            account["session_cookies"] = {}
            account.pop("last_success_at", None)
        state["accounts"][resolved] = account
        if not state["active_account_id"]:
            state["active_account_id"] = resolved
        return state

    update_provider_state("nug", apply)
    reload_config()
    return resolved


def set_active_account(account_id: str) -> None:
    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        if account_id not in state["accounts"]:
            raise KeyError("NUG 账户不存在")
        state["active_account_id"] = account_id
        return state

    update_provider_state("nug", apply)
    reload_config()


def _persist_session(account_id: str, cookies: dict[str, str]) -> None:
    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        account = state["accounts"].get(account_id)
        if not isinstance(account, dict):
            return state
        account["session_cookies"] = dict(cookies)
        account["last_success_at"] = datetime.now(timezone.utc).isoformat()
        state["accounts"][account_id] = account
        return state

    try:
        update_provider_state("nug", apply)
    except VaultError:
        logger.warning("[nug] 无法写入加密会话 Vault")


def _get_client() -> NUGClient | None:
    global _CLIENT, _CLIENT_ACCOUNT_ID
    if not _enabled():
        return None
    try:
        account = get_nug_account()
    except VaultError:
        return None
    account_id = str(account.get("_account_id") or "")
    if not account_id or not all([account.get("url"), account.get("username"), account.get("password")]):
        return None
    if _CLIENT is not None and _CLIENT_ACCOUNT_ID == account_id:
        return _CLIENT
    _CLIENT = NUGClient(
        str(account["url"]),
        str(account["username"]),
        str(account["password"]),
        session_cookies=account.get("session_cookies") if isinstance(account.get("session_cookies"), dict) else {},
        on_session_update=lambda cookies: _persist_session(account_id, cookies),
    )
    _CLIENT_ACCOUNT_ID = account_id
    return _CLIENT


# ============================================================
# Provider 数据能力
# ============================================================


def get_balance() -> dict | None:
    global _LAST_SUCCESS_AT, _LAST_ERROR
    client = _get_client()
    if client is None:
        return None
    data = client.get_balance()
    if data is None:
        _LAST_ERROR = "获取余额失败"
        return None
    _LAST_ERROR = None
    _LAST_SUCCESS_AT = datetime.now(timezone.utc).isoformat()
    return {"balance": str(data.get("balance", 0)), "currency": "USD", "details": {}}


def get_usage_summary() -> dict | None:
    return None


def get_channel_breakdown(days: int = 7) -> list | None:
    global _LAST_SUCCESS_AT, _LAST_ERROR
    client = _get_client()
    if client is None:
        return None
    rows = client.get_channel_breakdown(days=days)
    if rows is None:
        _LAST_ERROR = "获取 channel breakdown 失败"
        return None
    _LAST_ERROR = None
    _LAST_SUCCESS_AT = datetime.now(timezone.utc).isoformat()
    return [{**row, "currency": row.get("currency", "USD")} if isinstance(row, dict) else row for row in rows]


# ============================================================
# 认证生命周期
# ============================================================


def get_auth_status() -> dict[str, Any]:
    if not _enabled():
        return {
            "status": "disabled", "authenticated": False, "active_account_id": "",
            "active_account_label": "", "last_error": None, "last_refresh_at": None,
            "expires_at": None, "refresh_state": get_refresh_status("nug"),
        }
    try:
        account = get_nug_account()
    except VaultError:
        account = {}
        error = "凭据 Vault 无法解密"
    else:
        error = None
    has_credentials = bool(account.get("url") and account.get("username") and account.get("password"))
    return {
        "status": "authenticated" if has_credentials and not _LAST_ERROR else ("needs_login" if not has_credentials else "error"),
        "authenticated": has_credentials and not bool(_LAST_ERROR),
        "active_account_id": account.get("_account_id", ""),
        "active_account_label": account.get("label") or account.get("username") or "",
        "last_error": error or _LAST_ERROR,
        "last_refresh_at": _LAST_SUCCESS_AT,
        "expires_at": None,
        "refresh_state": get_refresh_status("nug"),
    }


def test_connection(account_id: str | None = None) -> dict[str, Any]:
    try:
        account = get_nug_account(account_id)
    except VaultError:
        return {"ok": False, "status": "needs_login", "message": "凭据 Vault 无法解密"}
    if not all([account.get("url"), account.get("username"), account.get("password")]):
        return {"ok": False, "status": "needs_login", "message": "账户没有完整登录凭据"}
    client = NUGClient(
        str(account["url"]), str(account["username"]), str(account["password"]),
        session_cookies=account.get("session_cookies") if isinstance(account.get("session_cookies"), dict) else {},
        on_session_update=lambda cookies: _persist_session(str(account["_account_id"]), cookies),
    )
    ok = client.get_balance() is not None
    return {"ok": ok, "status": "ok" if ok else "error", "message": "连接成功" if ok else "无法登录或读取 NUG 账户"}


@auto_refresh(interval_seconds=300, mode="both")
def refresh_credentials() -> RefreshResult:
    global _LAST_ERROR, _LAST_SUCCESS_AT
    if not _enabled():
        return RefreshResult.skipped("NUG Provider 已禁用")
    client = _get_client()
    if client is None:
        _LAST_ERROR = "没有可用 NUG 活动账户"
        return RefreshResult.needs_login("请在认证页添加并选择 NUG 账户")
    if client.get_balance() is None:
        _LAST_ERROR = "NUG 登录或会话刷新失败"
        return RefreshResult.needs_login("NUG 会话不可用，请重新登录")
    _LAST_ERROR = None
    _LAST_SUCCESS_AT = datetime.now(timezone.utc).isoformat()
    return RefreshResult.unchanged("NUG 会话有效")


def logout(account_id: str | None = None) -> dict[str, Any]:
    target = account_id or get_nug_account().get("_account_id")
    if not target:
        return {"ok": False, "message": "账户不存在"}

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        account = state["accounts"].get(target)
        if not isinstance(account, dict):
            raise KeyError("NUG 账户不存在")
        account["session_cookies"] = {}
        account["password"] = ""
        state["accounts"][target] = account
        return state

    update_provider_state("nug", apply)
    reload_config()
    return {"ok": True, "message": "已登出；再次使用请重新输入密码"}


def can_delete_account(account_id: str) -> list[dict[str, str]]:
    state = _state()
    if account_id not in state["accounts"]:
        return [{"type": "missing", "label": "账户不存在"}]
    if state["active_account_id"] == account_id and len(state["accounts"]) > 1:
        return [{"type": "active", "label": "该账户当前为活动账户，请先切换活动账户"}]
    return []


def delete_account(account_id: str) -> None:
    refs = can_delete_account(account_id)
    if refs:
        raise ValueError("账户仍被引用")

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        state["accounts"].pop(account_id, None)
        if state["active_account_id"] == account_id:
            state["active_account_id"] = next(iter(state["accounts"]), "")
        return state

    update_provider_state("nug", apply)
    reload_config()


def reload_config() -> None:
    global _CLIENT, _CLIENT_ACCOUNT_ID, _LAST_SUCCESS_AT, _LAST_ERROR
    _CLIENT = None
    _CLIENT_ACCOUNT_ID = ""
    _LAST_SUCCESS_AT = None
    _LAST_ERROR = None


# ============================================================
# Provider 自定义认证页面
# ============================================================


def register_auth_routes(router: Any) -> None:
    from flask import jsonify, render_template_string, request

    @router.page("")
    def auth_page():
        return render_template_string(_AUTH_PAGE)

    @router.api("state")
    def auth_state():
        return jsonify({"status": get_auth_status(), "accounts": list_accounts()})

    @router.api("accounts", methods=["POST"])
    def auth_create():
        payload = request.get_json(silent=True) or {}
        try:
            account_id = save_account(payload)
            return jsonify({"ok": True, "account_id": account_id})
        except (ValueError, VaultError) as exc:
            return jsonify({"error": str(exc)}), 400

    @router.api("accounts/<account_id>", methods=["POST"])
    def auth_update(account_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            save_account(payload, account_id)
            return jsonify({"ok": True})
        except (ValueError, VaultError) as exc:
            return jsonify({"error": str(exc)}), 400

    @router.api("accounts/<account_id>/activate", methods=["POST"])
    def auth_activate(account_id: str):
        try:
            set_active_account(account_id)
            return jsonify({"ok": True})
        except (KeyError, VaultError):
            return jsonify({"error": "账户不存在或 Vault 不可用"}), 404

    @router.api("accounts/<account_id>/test", methods=["POST"])
    def auth_test(account_id: str):
        return jsonify(test_connection(account_id))

    @router.api("accounts/<account_id>/refresh", methods=["POST"])
    def auth_refresh(account_id: str):
        if get_nug_account().get("_account_id") != account_id:
            return jsonify({"error": "请先切换为活动账户"}), 409
        result = refresh_credentials(_credential_force=True)
        return jsonify({"ok": result.success, "result": result.__dict__})

    @router.api("accounts/<account_id>/logout", methods=["POST"])
    def auth_logout(account_id: str):
        try:
            return jsonify(logout(account_id))
        except (KeyError, VaultError):
            return jsonify({"error": "账户不存在或 Vault 不可用"}), 404

    @router.api("accounts/<account_id>/delete", methods=["POST"])
    def auth_delete(account_id: str):
        refs = can_delete_account(account_id)
        if refs:
            return jsonify({"error": "该账户仍被引用", "references": refs}), 409
        try:
            delete_account(account_id)
            return jsonify({"ok": True})
        except (KeyError, VaultError, ValueError):
            return jsonify({"error": "无法删除账户"}), 400


_AUTH_PAGE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>NUG 认证</title>
<style>body{font:14px system-ui;margin:28px;background:#111827;color:#e5e7eb;max-width:900px}input,button{padding:8px;margin:4px;border-radius:6px;border:1px solid #4b5563;background:#1f2937;color:inherit}button{cursor:pointer}.card{border:1px solid #374151;padding:12px;margin:10px 0;border-radius:8px}.muted{color:#9ca3af}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}</style>
<h1>NUG 账户认证</h1><p id='status' class='muted'>正在读取…</p><h2>添加账户</h2><div class='grid'><input id='label' placeholder='显示名'><input id='url' placeholder='服务 URL'><input id='username' placeholder='用户名'><input id='password' type='password' placeholder='密码'></div><button onclick='create()'>保存账户</button><h2>账户列表</h2><div id='accounts'></div>
<script>
const api='/auth/nug/api/';async function call(p,o={}){const r=await fetch(api+p,{headers:{'Content-Type':'application/json'},...o});return r.json()}function e(v){return String(v||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function load(){const d=await call('state');document.querySelector('#status').textContent='状态：'+(d.status?.status||'unknown')+(d.status?.active_account_label?' · '+d.status.active_account_label:'');document.querySelector('#accounts').innerHTML=(d.accounts||[]).map(a=>`<div class=card><b>${e(a.label)}</b>${a.active?'（活动）':''}<div class=muted>${e(a.url)} · ${e(a.username)}</div><button onclick="act('${a.id}')">设为活动</button><button onclick="test('${a.id}')">测试</button><button onclick="ref('${a.id}')">刷新</button><button onclick="out('${a.id}')">登出</button><button onclick="del('${a.id}')">删除</button></div>`).join('')||'<p class=muted>暂无账户</p>'}async function create(){const d=await call('accounts',{method:'POST',body:JSON.stringify({label:label.value,url:url.value,username:username.value,password:password.value})});if(d.error)alert(d.error);else{password.value='';load()}}async function act(id){await call(`accounts/${id}/activate`,{method:'POST'});load()}async function test(id){alert((await call(`accounts/${id}/test`,{method:'POST'})).message||'完成')}async function ref(id){const d=await call(`accounts/${id}/refresh`,{method:'POST'});alert(d.error||d.result?.message||'完成');load()}async function out(id){const d=await call(`accounts/${id}/logout`,{method:'POST'});alert(d.error||d.message||'完成');load()}async function del(id){if(confirm('删除该账户？')){const d=await call(`accounts/${id}/delete`,{method:'POST'});if(d.error)alert(d.error);load()}}load();
</script>"""


# ============================================================
# 通用状态
# ============================================================


def get_status() -> dict:
    auth = get_auth_status()
    enabled = _enabled()
    status = "disabled" if not enabled else ("ok" if auth["authenticated"] else ("error" if auth["status"] in {"needs_login", "error"} else "unknown"))
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "error": auth.get("last_error"),
        "last_success_at": _LAST_SUCCESS_AT,
    }
