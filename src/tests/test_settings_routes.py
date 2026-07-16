"""Tests for loopback-only settings routes."""

from __future__ import annotations

from app.factory import create_app
from features.settings import routes as settings_routes
from features.settings.schema import SettingsValidationError


def _app():
    return create_app({"TESTING": True})


def test_settings_page_and_assets_require_loopback():
    client = _app().test_client()
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/settings-assets/settings.js", environ_base={"REMOTE_ADDR": "::1"}).status_code == 200
    assert client.get("/settings-assets/dashboard.js", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 404
    assert client.get("/static/settings.js", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/settings", environ_base={"REMOTE_ADDR": "10.0.0.8"}).status_code == 403
    rebound = client.get(
        "/settings",
        headers={"Host": "attacker.example", "Origin": "http://attacker.example"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert rebound.status_code == 403


def test_settings_get_response_is_no_store_and_uses_service(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "get_settings_payload",
        lambda: {"config": {"github_token": {"configured": True, "masked": "••••••"}}, "options": {}},
    )
    response = _app().test_client().get("/api/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.get_json()["config"]["github_token"]["configured"] is True


def test_settings_post_validates_and_broadcasts(monkeypatch):
    app = _app()
    called = []
    monkeypatch.setattr(settings_routes, "save_settings_payload", lambda payload: {"ok": True, "config": {}, "options": {}})
    monkeypatch.setattr(app.extensions["dashboard_runtime"].hub, "broadcast_settings_update", lambda: called.append(True))
    response = app.test_client().post(
        "/api/settings",
        json={"config": {}, "secrets": {}},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 200
    assert called == [True]


def test_settings_post_returns_structured_validation_error(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "save_settings_payload",
        lambda payload: (_ for _ in ()).throw(SettingsValidationError("时间无效", "dashboard.off_peak_badge")),
    )
    response = _app().test_client().post(
        "/api/settings",
        json={"config": {}, "secrets": {}},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == {
        "message": "时间无效",
        "field": "dashboard.off_peak_badge",
    }


def test_client_navigation_accepts_workspaces_and_keeps_legacy_pages(monkeypatch):
    app = _app()
    app.extensions["workspace_service"].create_blank("Secondary", workspace_id="secondary")
    calls = []

    def navigate(client_id, page="dashboard", *, workspace_id=None, url=None):
        calls.append((client_id, page, workspace_id, url))
        return True

    monkeypatch.setattr(app.extensions["dashboard_runtime"].hub, "navigate_client", navigate)
    client = app.test_client()
    workspace = client.post(
        "/api/settings/clients/client-1/navigate",
        json={"workspace_id": "secondary"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert workspace.status_code == 200
    assert workspace.get_json() == {
        "ok": True,
        "page": "dashboard",
        "workspace_id": "secondary",
    }
    assert calls[-1] == ("client-1", "dashboard", "secondary", None)

    legacy = client.post(
        "/api/settings/clients/client-1/navigate",
        json={"page": "music"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert legacy.status_code == 200
    assert calls[-1] == ("client-1", "music", None, None)

    missing = client.post(
        "/api/settings/clients/client-1/navigate",
        json={"workspace_id": "missing"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert missing.status_code == 404
    assert missing.get_json()["error"]["message"] == "目标工作区不存在"


def test_reveal_requires_loopback_and_post_protection(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "reveal_secret",
        lambda path, **kwargs: "revealed" if path == "github_token" else "",
    )
    client = _app().test_client()
    blocked = client.post(
        "/api/settings/reveal",
        json={"path": "github_token"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    assert blocked.status_code == 403
    allowed = client.post(
        "/api/settings/reveal",
        json={"path": "github_token"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert allowed.status_code == 200
    assert allowed.get_json() == {"path": "github_token", "value": "revealed"}
