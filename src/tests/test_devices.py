"""Tests for browser display-terminal registration and approval."""

from __future__ import annotations

from app.factory import create_app


DEVICE_A = "11111111-1111-4111-8111-111111111111"
DEVICE_B = "22222222-2222-4222-8222-222222222222"


def _app():
    return create_app({"TESTING": True})


def _session(client, device_id, **extra):
    return client.post(
        "/api/device/session",
        json={"device_id": device_id, "display_name": "screen", "page": "dashboard", **extra},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )


def test_device_session_registers_pending_and_protects_workspace_and_data():
    app = _app()
    client = app.test_client()

    blocked = client.get("/api/workspaces/main")
    assert blocked.status_code == 403
    assert blocked.get_json()["error"]["code"] == "device_required"

    pending = _session(client, DEVICE_A)
    assert pending.status_code == 200
    body = pending.get_json()
    assert body["approved"] is False
    assert body["reason"] == "device_pending"
    assert body["device"]["status"] == "pending"

    denied = client.get(
        "/api/workspaces/main",
        headers={"X-Dashboard-Device": DEVICE_A},
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "device_pending"

    data_denied = client.get(
        "/api/data",
        headers={"X-Dashboard-Device": DEVICE_A},
    )
    assert data_denied.status_code == 403


def test_settings_can_approve_device_and_grant_assigned_workspace():
    app = _app()
    client = app.test_client()
    _session(client, DEVICE_A)

    listed = client.get("/api/settings/devices", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert listed.status_code == 200
    devices = listed.get_json()["devices"]
    assert any(item["id"] == DEVICE_A for item in devices)

    approved = client.patch(
        f"/api/settings/devices/{DEVICE_A}",
        json={"status": "approved", "workspace_id": "main", "note": "副屏A", "scale_mode": "fixed", "scale": 1.25},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert approved.status_code == 200
    payload = approved.get_json()
    assert payload["approved"] is True
    assert payload["device"]["note"] == "副屏A"
    assert payload["device"]["scale"] == 1.25

    session = _session(client, DEVICE_A)
    assert session.get_json()["approved"] is True
    assert session.get_json()["workspace_id"] == "main"

    manifest = client.get(
        "/api/workspaces/main",
        headers={"X-Dashboard-Device": DEVICE_A},
    )
    assert manifest.status_code == 200
    assert manifest.get_json()["id"] == "main"

    data = client.get(
        "/api/data",
        headers={"X-Dashboard-Device": DEVICE_A},
    )
    assert data.status_code == 200


def test_approved_device_cannot_read_unassigned_workspace(monkeypatch):
    app = _app()
    client = app.test_client()
    app.extensions["workspace_service"].create_blank("Secondary", workspace_id="secondary")
    _session(client, DEVICE_B)
    client.patch(
        f"/api/settings/devices/{DEVICE_B}",
        json={"status": "approved", "workspace_id": "main"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    denied = client.get(
        "/api/workspaces/secondary",
        headers={"X-Dashboard-Device": DEVICE_B},
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "workspace_not_assigned"


def test_settings_can_delete_registered_device():
    app = _app()
    client = app.test_client()
    _session(client, DEVICE_A)
    listed = client.get("/api/settings/devices", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert any(item["id"] == DEVICE_A for item in listed.get_json()["devices"])

    deleted = client.delete(
        f"/api/settings/devices/{DEVICE_A}",
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert deleted.status_code == 200
    payload = deleted.get_json()
    assert payload["ok"] is True
    assert payload["deleted"] is True
    assert payload["device"]["id"] == DEVICE_A

    listed_again = client.get("/api/settings/devices", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert all(item["id"] != DEVICE_A for item in listed_again.get_json()["devices"])

    missing = client.delete(
        f"/api/settings/devices/{DEVICE_A}",
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert missing.status_code == 404
