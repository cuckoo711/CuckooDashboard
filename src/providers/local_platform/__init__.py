"""本地 MiMo 兼容平台 Provider：多账户、URL 凭据引用和 JWT Vault 缓存。"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from providers.runtime_config import get_provider_config, set_provider_config
from core.credentials import VaultError, get_provider_state, update_provider_state
from providers.auth import RefreshResult, auto_refresh, get_refresh_status
from providers.local_platform.client import LocalMimoAPI

logger = logging.getLogger("cuckoo.providers.local_platform")

PROVIDER_ID = "local_platform"
CAPABILITIES = ["token_plan", "daily_usage"]

CONFIG_SCHEMA = {
    "config_key": "local_platform",
    "title": "本地平台",
    "description": "MiMo 兼容本地实例。服务地址通过 credential_ref 引用 DPAPI Vault 中的账户。",
    "order": 20,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": False},
        {
            "key": "urls",
            "label": "实例列表",
            "type": "object_list",
            "identity_key": "url",
            "item_fields": [
                {"key": "url", "label": "服务 URL", "type": "url"},
                {"key": "credential_ref", "label": "账户 ID", "type": "string", "default": ""},
            ],
            "default": [],
        },
    ],
    "status_only_auth": True,
}

AUTH_DESCRIPTOR = {
    "title": "本地平台账户认证",
    "auth_path": "/auth/local_platform/",
    "custom_ui": True,
}


_local_apis: list[LocalMimoAPI] | None = None
_last_success_at: str | None = None
_last_error: str | None = None
_last_available_count: int | None = None


def _normalise_state(value: object) -> dict[str, Any]:
    state = dict(value) if isinstance(value, dict) else {}
    state["accounts"] = dict(state.get("accounts") or {})
    active = state.get("active_account_id")
    state["active_account_id"] = active if isinstance(active, str) else ""
    return state


def _state() -> dict[str, Any]:
    return _normalise_state(get_provider_state("local_platform", {}))


def _enabled() -> bool:
    config = get_provider_config("local_platform", {})
    return bool(config.get("enabled")) if isinstance(config, dict) else False


def get_local_account(account_id: str | None = None) -> dict[str, Any]:
    state = _state()
    resolved = account_id or state["active_account_id"]
    value = state["accounts"].get(resolved)
    if not isinstance(value, dict):
        return {}
    result = dict(value)
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
            "username": str(account.get("username") or ""),
            "configured": bool(account.get("password")),
            "active": account_id == active,
            "token_count": len(account.get("tokens") or {}) if isinstance(account.get("tokens"), dict) else 0,
        })
    return sorted(result, key=lambda item: (not item["active"], item["label"].casefold(), item["id"]))


def save_account(payload: dict[str, Any], account_id: str | None = None) -> str:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    label = str(payload.get("label") or "").strip()
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    resolved = account_id or f"local-{uuid.uuid4().hex[:12]}"

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        prior = state["accounts"].get(resolved)
        account = dict(prior) if isinstance(prior, dict) else {}
        changed = account.get("username") != username or account.get("password") != password
        account.update({
            "label": label or str(account.get("label") or username),
            "username": username,
            "password": password,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        if changed:
            account["tokens"] = {}
        state["accounts"][resolved] = account
        if not state["active_account_id"]:
            state["active_account_id"] = resolved
        return state

    update_provider_state("local_platform", apply)
    reload_config()
    return resolved


def set_active_account(account_id: str) -> None:
    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        if account_id not in state["accounts"]:
            raise KeyError("本地平台账户不存在")
        state["active_account_id"] = account_id
        return state

    update_provider_state("local_platform", apply)
    reload_config()


def _config_urls() -> list[dict[str, str]]:
    config = get_provider_config("local_platform", {})
    raw_urls = config.get("urls", []) if isinstance(config, dict) else []
    rows: list[dict[str, str]] = []
    for raw in raw_urls if isinstance(raw_urls, list) else []:
        if isinstance(raw, str):
            rows.append({"url": raw.rstrip("/"), "credential_ref": ""})
        elif isinstance(raw, dict) and raw.get("url"):
            rows.append({
                "url": str(raw.get("url") or "").strip().rstrip("/"),
                "credential_ref": str(raw.get("credential_ref") or "").strip(),
            })
    return [row for row in rows if row["url"]]


def list_urls() -> list[dict[str, str]]:
    labels = {item["id"]: item["label"] for item in list_accounts()}
    return [{**row, "credential_label": labels.get(row["credential_ref"], "")} for row in _config_urls()]


def _valid_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("服务地址必须是 http:// 或 https:// URL")
    return value.rstrip("/")


def save_url(url: str, credential_ref: str) -> None:
    url = _valid_url(str(url).strip())
    if not credential_ref or not get_local_account(credential_ref):
        raise ValueError("请选择存在的账户")
    config = dict(get_provider_config("local_platform", {}) or {})
    rows = _config_urls()
    replaced = False
    for row in rows:
        if row["url"] == url:
            row["credential_ref"] = credential_ref
            replaced = True
            break
    if not replaced:
        rows.append({"url": url, "credential_ref": credential_ref})
    config["urls"] = rows
    set_provider_config("local_platform", config)
    reload_config()


def delete_url(url: str) -> None:
    target = str(url).strip().rstrip("/")
    config = dict(get_provider_config("local_platform", {}) or {})
    config["urls"] = [row for row in _config_urls() if row["url"] != target]
    set_provider_config("local_platform", config)
    reload_config()


def _get_apis() -> list[LocalMimoAPI]:
    global _local_apis
    if _local_apis is not None:
        return _local_apis
    _local_apis = []
    if not _enabled():
        return _local_apis
    for row in _config_urls():
        account = get_local_account(row.get("credential_ref") or None)
        cached_tokens = account.get("tokens") if isinstance(account.get("tokens"), dict) else {}
        has_cached_token = bool(cached_tokens.get(row["url"]))
        if not all([account.get("username"), account.get("password")]) and not has_cached_token:
            continue
        _local_apis.append(LocalMimoAPI(
            row["url"],
            str(account["username"]),
            str(account["password"]),
            account_id=str(account.get("_account_id") or ""),
        ))
    logger.info("[local] 已配置 %s 个可用本地平台实例", len(_local_apis))
    return _local_apis


def reload_config() -> None:
    global _local_apis, _last_success_at, _last_error, _last_available_count
    _local_apis = None
    _last_success_at = None
    _last_error = None
    _last_available_count = None


# ============================================================
# Provider 数据能力
# ============================================================


def _empty_usage() -> dict[str, int | float]:
    return {
        "requestCount": 0, "totalInputTokens": 0, "totalOutputTokens": 0,
        "totalCacheReadTokens": 0, "totalTokens": 0, "totalReasoningTokens": 0,
        "totalCost": 0, "errorCount": 0, "meterUsage": 0,
    }


def get_plan_detail() -> dict | None:
    return None


def get_plan_usage() -> dict | None:
    return None


def get_daily_detail(year: int | None = None, month: int | None = None) -> dict | None:
    return None


def get_model_breakdown() -> list | None:
    return None


def aggregate_today_usage() -> dict | None:
    global _last_success_at, _last_error, _last_available_count
    usage = _empty_usage()
    available_count = 0
    try:
        for api in _get_apis():
            today = api.get_today_usage()
            if not today:
                continue
            available_count += 1
            for key in usage:
                usage[key] += today.get(key, 0)
        _last_available_count = available_count
        _last_error = None if available_count else "未获取到本地平台数据"
        if available_count:
            _last_success_at = str(time.time())
            return usage
        return None
    except Exception as exc:
        _last_available_count = 0
        _last_error = str(exc)
        return None


def get_today_usage() -> dict[str, int | str] | None:
    """将本地兼容平台聚合结果转换为 Provider 无关的今日用量。"""
    usage = aggregate_today_usage()
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("totalInputTokens", 0) or 0)
    output_tokens = int(usage.get("totalOutputTokens", 0) or 0)
    cached_input_tokens = int(usage.get("totalCacheReadTokens", 0) or 0)
    total_tokens = int(usage.get("totalTokens", 0) or 0)
    source_count = int(_last_available_count or 0)
    if not source_count:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": max(0, input_tokens - cached_input_tokens),
        "total_tokens": total_tokens,
        "source_count": source_count,
        "period": "today",
    }


# ============================================================
# 认证生命周期
# ============================================================


def get_auth_status() -> dict[str, Any]:
    if not _enabled():
        return {
            "status": "disabled", "authenticated": False, "active_account_id": "",
            "active_account_label": "", "last_error": None, "last_refresh_at": None,
            "expires_at": None, "refresh_state": get_refresh_status("local_platform"),
        }
    try:
        active = get_local_account()
    except VaultError:
        active = {}
        error = "凭据 Vault 无法解密"
    else:
        error = None
    configured = bool(active.get("username") and active.get("password"))
    return {
        "status": "authenticated" if configured else "needs_login",
        "authenticated": configured,
        "active_account_id": active.get("_account_id", ""),
        "active_account_label": active.get("label") or active.get("username") or "",
        "last_error": error or _last_error,
        "last_refresh_at": _last_success_at,
        "expires_at": None,
        "refresh_state": get_refresh_status("local_platform"),
    }


def test_connection(account_id: str | None = None, url: str | None = None) -> dict[str, Any]:
    account = get_local_account(account_id)
    target_url = url or next((row["url"] for row in _config_urls() if row.get("credential_ref") == account.get("_account_id")), "")
    if not target_url or not all([account.get("username"), account.get("password")]):
        return {"ok": False, "status": "needs_login", "message": "请选择已绑定 URL 的完整账户"}
    api = LocalMimoAPI(target_url, str(account["username"]), str(account["password"]), account_id=str(account.get("_account_id") or ""))
    ok = api._ensure_token()
    return {"ok": ok, "status": "ok" if ok else "error", "message": "连接成功" if ok else "无法登录本地平台"}


@auto_refresh(interval_seconds=300, mode="on_demand")
def refresh_credentials() -> RefreshResult:
    if not _enabled():
        return RefreshResult.skipped("本地平台 Provider 已禁用")
    apis = _get_apis()
    if not apis:
        return RefreshResult.needs_login("请配置服务 URL 并绑定账户")
    successes = sum(1 for api in apis if api._ensure_token())
    if not successes:
        return RefreshResult.needs_login("本地平台 JWT 无法刷新，请检查账户")
    return RefreshResult.unchanged(f"已验证 {successes}/{len(apis)} 个本地平台会话")


def logout(account_id: str | None = None) -> dict[str, Any]:
    target = account_id or get_local_account().get("_account_id")
    if not target:
        return {"ok": False, "message": "账户不存在"}

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        account = state["accounts"].get(target)
        if not isinstance(account, dict):
            raise KeyError("账户不存在")
        account["password"] = ""
        account["tokens"] = {}
        state["accounts"][target] = account
        return state

    update_provider_state("local_platform", apply)
    reload_config()
    return {"ok": True, "message": "已登出；再次使用请重新输入密码"}


def can_delete_account(account_id: str) -> list[dict[str, str]]:
    refs = [{"type": "url", "label": row["url"]} for row in _config_urls() if row.get("credential_ref") == account_id]
    state = _state()
    if state["active_account_id"] == account_id and len(state["accounts"]) > 1:
        refs.append({"type": "active", "label": "该账户当前为活动账户，请先切换活动账户"})
    return refs


def delete_account(account_id: str) -> None:
    refs = can_delete_account(account_id)
    if refs:
        raise ValueError("账户仍被 URL 或活动账户引用")

    def apply(raw: dict[str, Any]) -> dict[str, Any]:
        state = _normalise_state(raw)
        if account_id not in state["accounts"]:
            raise KeyError("账户不存在")
        del state["accounts"][account_id]
        if state["active_account_id"] == account_id:
            state["active_account_id"] = next(iter(state["accounts"]), "")
        return state

    update_provider_state("local_platform", apply)
    reload_config()


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
        return jsonify({"status": get_auth_status(), "accounts": list_accounts(), "urls": list_urls()})

    @router.api("accounts", methods=["POST"])
    def auth_create_account():
        try:
            account_id = save_account(request.get_json(silent=True) or {})
            return jsonify({"ok": True, "account_id": account_id})
        except (ValueError, VaultError) as exc:
            return jsonify({"error": str(exc)}), 400

    @router.api("accounts/<account_id>", methods=["POST"])
    def auth_update_account(account_id: str):
        try:
            save_account(request.get_json(silent=True) or {}, account_id)
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
        # on_demand Provider 可由页面强制触发；业务逻辑仍由 Provider 方法决定。
        if not get_local_account(account_id):
            return jsonify({"error": "账户不存在"}), 404
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

    @router.api("urls", methods=["POST"])
    def auth_save_url():
        payload = request.get_json(silent=True) or {}
        try:
            save_url(str(payload.get("url") or ""), str(payload.get("credential_ref") or ""))
            return jsonify({"ok": True})
        except (ValueError, VaultError) as exc:
            return jsonify({"error": str(exc)}), 400

    @router.api("urls/delete", methods=["POST"])
    def auth_delete_url():
        payload = request.get_json(silent=True) or {}
        delete_url(str(payload.get("url") or ""))
        return jsonify({"ok": True})


_AUTH_PAGE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>本地平台认证</title>
<style>body{font:14px system-ui;margin:28px;background:#111827;color:#e5e7eb;max-width:1000px}input,select,button{padding:8px;margin:4px;border-radius:6px;border:1px solid #4b5563;background:#1f2937;color:inherit}button{cursor:pointer}.card{border:1px solid #374151;padding:12px;margin:10px 0;border-radius:8px}.muted{color:#9ca3af}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}</style>
<h1>本地平台账户与实例</h1><p id='status' class='muted'>正在读取…</p><h2>添加账户</h2><div class=grid><input id=label placeholder='显示名'><input id=username placeholder='用户名'><input id=password type=password placeholder='密码'></div><button onclick='createAccount()'>保存账户</button><h2>绑定实例 URL</h2><div class=grid><input id=url placeholder='http://host:7778'><select id=ref></select></div><button onclick='saveUrl()'>保存 URL 绑定</button><h2>账户</h2><div id=accounts></div><h2>实例 URL</h2><div id=urls></div>
<script>
const api='/auth/local_platform/api/';let state={};async function call(p,o={}){const r=await fetch(api+p,{headers:{'Content-Type':'application/json'},...o});return r.json()}function e(v){return String(v||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function load(){state=await call('state');document.querySelector('#status').textContent='状态：'+(state.status?.status||'unknown')+(state.status?.active_account_label?' · '+state.status.active_account_label:'');ref.innerHTML=(state.accounts||[]).map(a=>`<option value='${a.id}'>${e(a.label)}${a.active?'（活动）':''}</option>`).join('');accounts.innerHTML=(state.accounts||[]).map(a=>`<div class=card><b>${e(a.label)}</b>${a.active?'（活动）':''}<div class=muted>${e(a.username)} · ${a.token_count||0} 个 JWT 缓存</div><button onclick="act('${a.id}')">设为活动</button><button onclick="test('${a.id}')">测试</button><button onclick="refresh('${a.id}')">刷新</button><button onclick="logout('${a.id}')">登出</button><button onclick="del('${a.id}')">删除</button></div>`).join('')||'<p class=muted>暂无账户</p>';urls.innerHTML=(state.urls||[]).map(x=>`<div class=card><b>${e(x.url)}</b><div class=muted>账户：${e(x.credential_label||x.credential_ref)}</div><button onclick="removeUrl('${x.url}')">解绑 URL</button></div>`).join('')||'<p class=muted>暂无实例 URL</p>'}async function createAccount(){const d=await call('accounts',{method:'POST',body:JSON.stringify({label:label.value,username:username.value,password:password.value})});if(d.error)alert(d.error);else{password.value='';load()}}async function saveUrl(){const d=await call('urls',{method:'POST',body:JSON.stringify({url:url.value,credential_ref:ref.value})});if(d.error)alert(d.error);else load()}async function act(id){await call(`accounts/${id}/activate`,{method:'POST'});load()}async function test(id){alert((await call(`accounts/${id}/test`,{method:'POST'})).message||'完成')}async function refresh(id){const d=await call(`accounts/${id}/refresh`,{method:'POST'});alert(d.error||d.result?.message||'完成');load()}async function logout(id){const d=await call(`accounts/${id}/logout`,{method:'POST'});alert(d.error||d.message||'完成');load()}async function del(id){if(confirm('删除该账户？')){const d=await call(`accounts/${id}/delete`,{method:'POST'});if(d.error)alert(d.error);load()}}async function removeUrl(u){await call('urls/delete',{method:'POST',body:JSON.stringify({url:u})});load()}load();
</script>"""


# ============================================================
# 通用状态
# ============================================================


def get_status() -> dict[str, Any]:
    auth = get_auth_status()
    enabled = _enabled()
    if not enabled:
        status = "disabled"
    elif _last_available_count is None:
        status = "unknown" if auth["authenticated"] else "error"
    elif _last_available_count > 0:
        status = "ok"
    else:
        status = "error"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "error": auth.get("last_error"),
        "last_success_at": _last_success_at,
    }
