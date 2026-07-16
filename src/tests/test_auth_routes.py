"""Provider 自定义认证路由的命名空间与安全边界测试。"""

from __future__ import annotations

from flask import Flask, jsonify

from providers.auth_routes import ProviderAuthRouter


def test_provider_router_keeps_page_and_api_in_fixed_namespace():
    app = Flask(__name__)
    guard_calls: list[str] = []
    router = ProviderAuthRouter(
        "atlas",
        require_loopback=lambda: guard_calls.append("loopback"),
        require_post_protection=lambda: guard_calls.append("post"),
    )

    @router.page("")
    def page():
        return "page"

    @router.api("state")
    def state():
        return jsonify({"ok": True})

    @router.api("change", methods=["POST"])
    def change():
        return jsonify({"ok": True})

    router.register(app)
    client = app.test_client()

    assert client.get("/auth/atlas/").status_code == 200
    assert client.get("/auth/atlas/api/state").get_json() == {"ok": True}
    assert client.post("/auth/atlas/api/change").status_code == 200
    assert guard_calls.count("loopback") == 3
    assert guard_calls.count("post") == 1
