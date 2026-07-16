"""Generic Provider APIs and dynamic Provider-owned route registration."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, request

from app.security import require_loopback_access, require_post_protection
from contracts.provider import ProviderStatus
from providers import get_auth_providers, get_providers
from providers.auth_routes import ProviderAuthRouter, ProviderPublicRouter
from services.dashboard_data_service import get_provider_public_data

logger = logging.getLogger("cuckoo.dashboard")

blueprint = Blueprint("providers", __name__)


@blueprint.route("/api/providers")
def api_providers():
    """返回已发现 Provider 的通用元数据与健康摘要。"""
    result = []
    for provider_id, provider in sorted(
        get_providers().items(), key=lambda item: item[0].casefold()
    ):
        get_status = getattr(provider, "get_status", None)
        try:
            raw_status = (
                get_status()
                if callable(get_status)
                else {"status": "unknown", "ok": False}
            )
        except Exception as exc:
            raw_status = {"status": "error", "ok": False, "error": str(exc)}
        if not isinstance(raw_status, dict):
            raw_status = {"status": "unknown", "ok": False}
        status = ProviderStatus.from_value(raw_status).to_provider_payload()
        result.append(
            {
                "id": provider_id,
                "capabilities": list(
                    getattr(provider, "CAPABILITIES", ()) or ()
                ),
                "status": status,
            }
        )
    return jsonify({"providers": result})


@blueprint.route("/api/providers/<provider_id>/<resource>")
def api_provider_data(provider_id: str, resource: str):
    """读取 Provider 声明的通用公开资源，不识别具体 Provider 名称。"""
    try:
        days = max(1, min(365, int(request.args.get("days", 7))))
    except (TypeError, ValueError):
        abort(400)
    data = get_provider_public_data(provider_id, resource, days=days)
    if data is None:
        abort(404)
    return jsonify({"provider": provider_id, "resource": resource, "data": data})


def register_provider_routes(app) -> None:
    """为当前 app 动态挂载 Provider 自己拥有的认证与公开路由。"""
    for provider_id, provider in get_auth_providers().items():
        register = getattr(provider, "register_auth_routes", None)
        if not callable(register):
            continue
        try:
            router = ProviderAuthRouter(
                provider_id,
                require_loopback=require_loopback_access,
                require_post_protection=require_post_protection,
            )
            register(router)
            router.register(app)
            logger.info("[auth] 已挂载 Provider 认证路由: %s", provider_id)
        except Exception:
            logger.exception("[auth] 挂载 Provider %s 认证路由失败", provider_id)

    for provider_id, provider in get_providers().items():
        register = getattr(provider, "register_public_routes", None)
        if not callable(register):
            continue
        try:
            router = ProviderPublicRouter(
                provider_id,
                require_post_protection=require_post_protection,
            )
            register(router)
            router.register(app)
            logger.info("[provider-api] 已挂载 Provider 公开路由: %s", provider_id)
        except Exception:
            logger.exception("[provider-api] 挂载 Provider %s 公开路由失败", provider_id)
