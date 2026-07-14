"""Tests for loopback-only settings routes."""

from __future__ import annotations

import dashboard
from services.settings_service import SettingsValidationError


def _client():
    return dashboard.app.test_client()


def test_settings_page_and_assets_require_loopback():
    client = _client()
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert client.get("/settings-assets/settings.js", environ_base={"REMOTE_ADDR": "::1"}).status_code == 200
    assert client.get("/settings-assets/dashboard.js", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 404
    assert client.get("/static/settings.js", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/settings", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/settings", environ_base={"REMOTE_ADDR": "10.0.0.8"}).status_code == 403


def test_settings_get_response_is_no_store_and_uses_service(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "get_settings_payload",
        lambda: {"config": {"github_token": {"configured": True, "masked": "••••••"}}, "options": {}},
    )
    response = _client().get("/api/settings", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.get_json()["config"]["github_token"]["configured"] is True


def test_settings_post_validates_and_broadcasts(monkeypatch):
    called = []
    monkeypatch.setattr(dashboard, "save_settings_payload", lambda payload: {"ok": True, "config": {}, "options": {}})
    monkeypatch.setattr(dashboard, "_broadcast_settings_update", lambda: called.append(True))
    response = _client().post(
        "/api/settings",
        json={"config": {}, "secrets": {}},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 200
    assert called == [True]


def test_settings_post_returns_structured_validation_error(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "save_settings_payload",
        lambda payload: (_ for _ in ()).throw(SettingsValidationError("时间无效", "dashboard.off_peak_badge")),
    )
    response = _client().post(
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


def test_reveal_requires_loopback_and_post_protection(monkeypatch):
    monkeypatch.setattr(dashboard, "reveal_secret", lambda path: "revealed" if path == "github_token" else "")
    client = _client()
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
