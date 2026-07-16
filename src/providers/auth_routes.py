"""Provider 自定义认证页面的受限 Flask 路由容器。"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from flask import Blueprint, abort, send_from_directory


class ProviderAuthRouter:
    """为一个 Provider 分配固定的认证页面、API 与静态资源命名空间。

    Provider 只能通过 ``page`` / ``api`` 在自己的 Blueprint 中注册路由。每个请求
    都由 Dashboard 提供的 loopback/POST 防护函数包裹，Provider 不需要也不能绕开
    设置页的本地访问边界。
    """

    def __init__(
        self,
        provider_id: str,
        *,
        require_loopback: Callable[[], None],
        require_post_protection: Callable[[], None],
    ) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", provider_id):
            raise ValueError("Provider 认证路由 ID 只能包含字母、数字、_ 和 -")
        safe_id = provider_id.replace("-", "_")
        self.provider_id = provider_id
        self.page_prefix = f"/auth/{provider_id}"
        self.api_prefix = f"/auth/{provider_id}/api"
        self.asset_prefix = f"/settings-provider-assets/{provider_id}"
        self._require_loopback = require_loopback
        self._require_post_protection = require_post_protection
        self._page_blueprint = Blueprint(
            f"provider_auth_page_{safe_id}",
            __name__,
            url_prefix=self.page_prefix,
        )
        self._api_blueprint = Blueprint(
            f"provider_auth_api_{safe_id}",
            __name__,
            url_prefix=self.api_prefix,
        )
        self._asset_blueprint = Blueprint(
            f"provider_auth_asset_{safe_id}",
            __name__,
            url_prefix=self.asset_prefix,
        )
        self._registered = False
        self._asset_dir: Path | None = None

    @staticmethod
    def _methods(methods: Iterable[str] | None) -> list[str]:
        values = [str(method).upper() for method in (methods or ["GET"])]
        return values or ["GET"]

    def _guard(self, fn: Callable[..., Any], methods: Iterable[str]) -> Callable[..., Any]:
        methods = self._methods(methods)

        @functools.wraps(fn)
        def guarded(*args: Any, **kwargs: Any):
            self._require_loopback()
            # 认证页面/API 的所有状态修改请求都复用 Dashboard POST 防护。
            from flask import request

            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                self._require_post_protection()
            return fn(*args, **kwargs)

        return guarded

    def page(self, rule: str = "", *, methods: Iterable[str] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """注册认证页面路由，默认对应 ``.../auth``。"""
        route = rule or "/"
        method_values = self._methods(methods)

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            endpoint = f"page_{fn.__name__}"
            self._page_blueprint.add_url_rule(route, endpoint, self._guard(fn, method_values), methods=method_values)
            return fn

        return decorate

    def api(self, rule: str, *, methods: Iterable[str] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """注册认证 API 路由，固定在 Provider 的 API 前缀内。"""
        route = rule if rule.startswith("/") else f"/{rule}"
        method_values = self._methods(methods)

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            endpoint = f"api_{fn.__name__}"
            self._api_blueprint.add_url_rule(route, endpoint, self._guard(fn, method_values), methods=method_values)
            return fn

        return decorate

    def use_static_directory(self, directory: str | Path) -> None:
        """允许 Provider 显式公开其认证页资源目录。"""
        path = Path(directory).resolve()
        if not path.is_dir():
            raise ValueError(f"Provider 认证静态目录不存在: {path}")
        self._asset_dir = path

        @self._asset_blueprint.route("/<path:filename>")
        def assets(filename: str):
            self._require_loopback()
            if self._asset_dir is None:
                abort(404)
            return send_from_directory(str(self._asset_dir), filename)

    def register(self, app: Any) -> None:
        if self._registered:
            return
        app.register_blueprint(self._page_blueprint)
        app.register_blueprint(self._api_blueprint)
        if self._asset_dir is not None:
            app.register_blueprint(self._asset_blueprint)
        self._registered = True
