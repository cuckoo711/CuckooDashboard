"""Tests for Provider-owned public API namespaces."""

from __future__ import annotations

from flask import Flask, jsonify

from providers.auth_routes import ProviderPublicRouter


def test_public_router_uses_only_generic_provider_namespace_and_guards_post():
    app = Flask(__name__)
    guard_calls: list[str] = []
    router = ProviderPublicRouter("atlas", require_post_protection=lambda: guard_calls.append("post"))

    @router.api("metrics")
    def metrics():
        return jsonify({"ok": True})

    @router.api("refresh", methods=["POST"])
    def refresh():
        return jsonify({"ok": True})

    router.register(app)
    client = app.test_client()

    assert client.get("/api/providers/atlas/custom/metrics").get_json() == {"ok": True}
    assert client.post("/api/providers/atlas/custom/refresh").status_code == 200
    assert guard_calls == ["post"]
    assert client.get("/api/atlas/metrics").status_code == 404
