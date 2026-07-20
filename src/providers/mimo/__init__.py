"""MiMo 官方平台 Provider：数据聚合、DPAPI 账户和网页扫码认证。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from providers.runtime_config import get_provider_config
from core.credentials import VaultError
from providers.mimo.implementation import (
    MiMoAPI,
    QRCodeLogin,
    XiaomiLoginError,
    delete_mimo_account,
    get_mimo_account,
    list_mimo_accounts,
    load_cookies,
    refresh_mimo_cookie,
    save_cookies,
    set_active_mimo_account,
)
from providers.auth import RefreshResult, auto_refresh, get_refresh_status
from providers.mimo.api import get_mimo_api, is_cookie_valid, reload_config as reload_api_config

logger = logging.getLogger("cuckoo.providers.mimo")

PROVIDER_ID = "mimo"
CAPABILITIES = ["token_plan", "balance", "api_usage", "daily_usage"]

CONFIG_SCHEMA = {
    "config_key": "mimo",
    "title": "MiMo",
    "description": "MiMo 官方平台。账户、Cookie 和刷新信息由 Windows DPAPI 凭据 Vault 管理。",
    "order": 10,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
    ],
    "status_only_auth": True,
}

AUTH_DESCRIPTOR = {
    "title": "MiMo 账户认证",
    "auth_path": "/auth/mimo/",
    "custom_ui": True,
}


_last_success_at: str | None = None
_last_error: str | None = None
_qr_lock = threading.RLock()
_qr_sessions: dict[str, dict[str, Any]] = {}


# ============================================================
# token_plan / balance / api_usage
# ============================================================


def get_plan_detail() -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        return api.get_token_plan_detail().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_plan_detail 失败: %s", exc)
        return None


def get_plan_usage() -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        return api.get_token_plan_usage().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_plan_usage 失败: %s", exc)
        return None


def get_daily_detail(year: int | None = None, month: int | None = None) -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        utc_now = datetime.now(timezone.utc)
        year = year or utc_now.year
        month = month or utc_now.month
        return api.session.get(
            f"https://platform.xiaomimimo.com/api/v1/usage/detail?year={year}&month={month}",
            timeout=15,
        ).json().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_daily_detail 失败: %s", exc)
        return None


def get_model_breakdown() -> list | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        return api.get_token_plan_usage_detail().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_model_breakdown 失败: %s", exc)
        return None


def get_user_profile() -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        return api.get_user_profile().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_user_profile 失败: %s", exc)
        return None


def get_balance() -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        data = api.get_balance().get("data", {})
        if not data:
            return None
        return {
            "balance": data.get("balance", "0"),
            "currency": data.get("currency", "CNY"),
            "details": {
                "cashBalance": data.get("cashBalance", "0"),
                "giftBalance": data.get("giftBalance", "0"),
                "frozenBalance": data.get("frozenBalance", "0"),
            },
        }
    except Exception as exc:
        logger.error("[MiMo] get_balance 失败: %s", exc)
        return None


def get_usage_summary() -> dict | None:
    api = get_mimo_api()
    if api is None:
        return None
    try:
        return api.get_usage().get("data")
    except Exception as exc:
        logger.error("[MiMo] get_usage_summary 失败: %s", exc)
        return None


def get_channel_breakdown(days: int = 7) -> list | None:
    return None


def get_today_usage() -> dict[str, int | str] | None:
    """将 MiMo 的月度明细适配为 Provider 无关的今日用量契约。"""
    daily_data = get_daily_detail() or {}
    rows = daily_data.get("tokenUsage") if isinstance(daily_data, dict) else None
    if not isinstance(rows, list):
        return None
    now = datetime.now(timezone.utc)
    target_key = f"{now.month:02d}-{now.day:02d}"
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5 or str(row[0]) != target_key:
            continue
        input_tokens = int(row[1] or 0)
        output_tokens = int(row[2] or 0)
        total_tokens = int(row[3] or 0)
        cached_input_tokens = int(row[4] or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "uncached_input_tokens": max(0, input_tokens - cached_input_tokens),
            "total_tokens": total_tokens,
            "source_count": 1,
            "period": "today",
        }
    return None


# ============================================================
# 认证生命周期
# ============================================================


def _enabled() -> bool:
    config = get_provider_config("mimo", {})
    return bool(config.get("enabled", True)) if isinstance(config, dict) else True


def _account_public(account_id: str | None = None) -> dict[str, Any]:
    account = get_mimo_account(account_id)
    if not account:
        return {}
    return {
        "id": account.get("_account_id", ""),
        "label": account.get("label") or account.get("userId") or account.get("_account_id", ""),
        "method": account.get("method", ""),
        "saved_at": account.get("saved_at", ""),
        "configured": bool(account.get("cookie")),
    }


def get_auth_status() -> dict[str, Any]:
    if not _enabled():
        return {
            "status": "disabled", "authenticated": False, "active_account_id": "",
            "active_account_label": "", "last_error": None, "last_refresh_at": None,
            "expires_at": None, "refresh_state": get_refresh_status("mimo"),
        }
    try:
        active = _account_public()
    except VaultError:
        active = {}
    if not active:
        state = "needs_login"
        authenticated = False
    else:
        validity = is_cookie_valid()
        state = "authenticated" if validity is not False else "needs_login"
        authenticated = validity is not False
    return {
        "status": state,
        "authenticated": authenticated,
        "active_account_id": active.get("id", ""),
        "active_account_label": active.get("label", ""),
        "last_error": _last_error,
        "last_refresh_at": _last_success_at,
        "expires_at": None,
        "refresh_state": get_refresh_status("mimo"),
    }


def test_connection(account_id: str | None = None) -> dict[str, Any]:
    account = get_mimo_account(account_id)
    cookie = str(account.get("cookie") or "")
    if not cookie:
        return {"ok": False, "status": "needs_login", "message": "该账户没有可用 Cookie"}
    try:
        response = MiMoAPI(cookie).get_user_profile()
        ok = response.get("code") != 401
        return {
            "ok": ok,
            "status": "ok" if ok else "needs_login",
            "message": "连接成功" if ok else "Cookie 已过期，请刷新或重新登录",
        }
    except Exception:
        return {"ok": False, "status": "error", "message": "无法连接 MiMo 平台"}


@auto_refresh(interval_seconds=300, mode="both")
def refresh_credentials() -> RefreshResult:
    """Provider 自行决定的 MiMo 自动刷新：仅活动账户、仅 passToken 流程。"""
    global _last_error, _last_success_at
    if not _enabled():
        return RefreshResult.skipped("MiMo Provider 已禁用")
    try:
        account = get_mimo_account()
    except VaultError:
        _last_error = "凭据 Vault 无法解密"
        return RefreshResult.needs_login("凭据 Vault 不可用，请重新认证")
    cookie = str(account.get("cookie") or "")
    if not cookie:
        _last_error = "没有活动 MiMo 账户"
        return RefreshResult.needs_login("请先登录 MiMo")
    try:
        probe = MiMoAPI(cookie).get_user_profile()
    except Exception:
        return RefreshResult.unchanged("当前未能验证 MiMo 会话")
    if probe.get("code") != 401:
        _last_error = None
        _last_success_at = datetime.now(timezone.utc).isoformat()
        return RefreshResult.unchanged("MiMo 会话有效")
    try:
        new_cookie = refresh_mimo_cookie(cookie)
    except Exception:
        new_cookie = None
    if not new_cookie:
        _last_error = "MiMo 会话已过期"
        return RefreshResult.needs_login("Cookie 无法自动刷新，请重新扫码登录")
    save_cookies(
        new_cookie,
        str(account.get("method") or "qr"),
        account,
        account_id=str(account.get("_account_id") or "") or None,
        label=str(account.get("label") or "") or None,
    )
    reload_api_config()
    _last_error = None
    _last_success_at = datetime.now(timezone.utc).isoformat()
    return RefreshResult.refreshed("MiMo Cookie 已刷新")


def logout(account_id: str | None = None) -> dict[str, Any]:
    account = get_mimo_account(account_id)
    if not account:
        return {"ok": False, "message": "账户不存在"}
    save_cookies("", str(account.get("method") or ""), account, account_id=account.get("_account_id"), label=account.get("label"))
    reload_api_config()
    return {"ok": True, "message": "已登出该账户"}


def can_delete_account(account_id: str) -> list[dict[str, str]]:
    active = _account_public()
    if active.get("id") == account_id and len(list_mimo_accounts()) > 1:
        return [{"type": "active", "label": "该账户当前为活动账户，请先切换活动账户"}]
    return []


# ============================================================
# MiMo 网页二维码认证
# ============================================================


def _qr_worker(login_id: str, qr: QRCodeLogin, label: str) -> None:
    global _last_error
    try:
        login_data = qr.wait_for_scan()
        cookie = qr.get_service_token(login_data)
        account_id = save_cookies(
            cookie,
            "qr",
            {
                "passToken": login_data.get("passToken", ""),
                "userId": str(login_data.get("userId", "")),
                "ssecurity": login_data.get("ssecurity", ""),
            },
            label=label or str(login_data.get("userId") or "MiMo 账户"),
        )
        with _qr_lock:
            _qr_sessions[login_id].update({"state": "success", "account_id": account_id, "message": "登录成功"})
        reload_api_config()
        _last_error = None
    except Exception:
        with _qr_lock:
            if login_id in _qr_sessions:
                _qr_sessions[login_id].update({"state": "failed", "message": "二维码登录失败或已超时"})
        _last_error = "二维码登录失败"


def start_qr_login(label: str = "") -> dict[str, Any]:
    qr = QRCodeLogin()
    if not qr.get_qr_code():
        raise XiaomiLoginError("无法获取 MiMo 二维码")
    login_id = uuid.uuid4().hex
    now = time.time()
    with _qr_lock:
        # 清理终态/过期的历史会话，长驻进程中该字典不能只增不减。
        for stale_id in [
            key for key, value in _qr_sessions.items()
            if value.get("state") in ("success", "failed")
            or now > float(value.get("expires_at") or 0) + 600
        ]:
            del _qr_sessions[stale_id]
        _qr_sessions[login_id] = {
            "state": "pending",
            "qr_image_url": qr._qr_image_url,
            "login_url": qr._login_url,
            "expires_at": time.time() + float(qr._timeout or 300),
            "message": "请使用小米账号扫描二维码",
        }
    threading.Thread(target=_qr_worker, args=(login_id, qr, label), daemon=True, name="mimo-qr-login").start()
    return {"id": login_id, **dict(_qr_sessions[login_id])}


def get_qr_login_state(login_id: str) -> dict[str, Any]:
    with _qr_lock:
        value = _qr_sessions.get(login_id)
        if not isinstance(value, dict):
            return {"state": "missing", "message": "登录会话不存在或已失效"}
        result = dict(value)
    if result.get("state") == "pending" and time.time() > float(result.get("expires_at") or 0):
        result.update({"state": "expired", "message": "二维码已过期，请重新获取"})
    return result


def register_auth_routes(router: Any) -> None:
    """为 MiMo 注册自定义二维码账户管理页。"""
    from flask import jsonify, render_template_string, request

    @router.page("")
    def auth_page():
        return render_template_string(_AUTH_PAGE)

    @router.api("state")
    def auth_state():
        return jsonify({"status": get_auth_status(), "accounts": list_mimo_accounts()})

    @router.api("qr/start", methods=["POST"])
    def auth_qr_start():
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(start_qr_login(str(payload.get("label") or "")))
        except Exception:
            return jsonify({"error": "无法获取 MiMo 登录二维码"}), 502

    @router.api("qr/<login_id>")
    def auth_qr_poll(login_id: str):
        return jsonify(get_qr_login_state(login_id))

    @router.api("accounts/<account_id>/activate", methods=["POST"])
    def auth_activate(account_id: str):
        try:
            set_active_mimo_account(account_id)
            reload_api_config()
            return jsonify({"ok": True})
        except (KeyError, VaultError):
            return jsonify({"error": "账户不存在或凭据 Vault 不可用"}), 404

    @router.api("accounts/<account_id>/test", methods=["POST"])
    def auth_test(account_id: str):
        return jsonify(test_connection(account_id))

    @router.api("accounts/<account_id>/refresh", methods=["POST"])
    def auth_refresh(account_id: str):
        active = _account_public()
        if active.get("id") != account_id:
            return jsonify({"error": "请先将该账户设为活动账户后刷新"}), 409
        result = refresh_credentials(_credential_force=True)
        return jsonify({"ok": result.success, "result": result.__dict__})

    @router.api("accounts/<account_id>/logout", methods=["POST"])
    def auth_logout(account_id: str):
        return jsonify(logout(account_id))

    @router.api("accounts/<account_id>/delete", methods=["POST"])
    def auth_delete(account_id: str):
        refs = can_delete_account(account_id)
        if refs:
            return jsonify({"error": "该账户仍被引用", "references": refs}), 409
        try:
            delete_mimo_account(account_id)
            reload_api_config()
            return jsonify({"ok": True})
        except (KeyError, VaultError):
            return jsonify({"error": "账户不存在或凭据 Vault 不可用"}), 404


_AUTH_PAGE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>MiMo 认证</title>
<style>body{font:14px system-ui;margin:28px;background:#111827;color:#e5e7eb;max-width:850px}button,input{padding:8px;margin:4px;border-radius:6px;border:1px solid #4b5563;background:#1f2937;color:inherit}button{cursor:pointer}.row{border:1px solid #374151;padding:12px;margin:10px 0;border-radius:8px}.muted{color:#9ca3af}img{max-width:260px;background:#fff;padding:8px}</style>
<h1>MiMo 账户认证</h1><p id='status' class='muted'>正在读取状态…</p><input id='label' placeholder='新账户显示名（可选）'><button onclick='start()'>获取扫码二维码</button><div id='qr'></div><h2>账户</h2><div id='accounts'></div>
<script>
const api='/auth/mimo/api/';
async function call(path,opts={}){const r=await fetch(api+path,{headers:{'Content-Type':'application/json'},...opts});return r.json()}
function esc(v){return String(v||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function load(){const d=await call('state');const s=d.status||{};document.querySelector('#status').textContent=`状态：${s.status||'unknown'}${s.active_account_label?' · '+s.active_account_label:''}`;document.querySelector('#accounts').innerHTML=(d.accounts||[]).map(a=>`<div class='row'><b>${esc(a.label)}</b> ${a.active?'（活动）':''}<div class='muted'>${esc(a.method)} · ${esc(a.saved_at)}</div><button onclick="act('${a.id}')">设为活动</button><button onclick="test('${a.id}')">测试</button><button onclick="refresh('${a.id}')">刷新</button><button onclick="logout('${a.id}')">登出</button><button onclick="del('${a.id}')">删除</button></div>`).join('')||'<p class=muted>还没有账户</p>'}
async function start(){const d=await call('qr/start',{method:'POST',body:JSON.stringify({label:document.querySelector('#label').value})});if(d.error){alert(d.error);return}document.querySelector('#qr').innerHTML=(d.qr_image_url?`<img src='${d.qr_image_url}'>`:'')+`<p>请扫码；若图片不可用可打开：<a target='_blank' href='${d.login_url}'>登录链接</a></p><p id='qrstate'></p>`;const timer=setInterval(async()=>{const x=await call('qr/'+d.id);document.querySelector('#qrstate').textContent=x.message||x.state;if(x.state!=='pending'){clearInterval(timer);load()}},1500)}
async function act(id){await call(`accounts/${id}/activate`,{method:'POST'});load()} async function test(id){alert((await call(`accounts/${id}/test`,{method:'POST'})).message||'完成')} async function refresh(id){const d=await call(`accounts/${id}/refresh`,{method:'POST'});alert(d.error||d.result?.message||'完成');load()} async function logout(id){await call(`accounts/${id}/logout`,{method:'POST'});load()} async function del(id){if(confirm('删除该账户？')){const d=await call(`accounts/${id}/delete`,{method:'POST'});if(d.error)alert(d.error);load()}} load();
</script>"""


# ============================================================
# 通用
# ============================================================


def reload_config() -> None:
    global _last_success_at, _last_error
    _last_success_at = None
    _last_error = None
    reload_api_config()


def get_status() -> dict:
    auth = get_auth_status()
    enabled = _enabled()
    status = "disabled" if not enabled else ("ok" if auth["authenticated"] else ("error" if auth["status"] == "needs_login" else "unknown"))
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": enabled,
        "error": auth.get("last_error"),
        "last_success_at": _last_success_at,
    }
