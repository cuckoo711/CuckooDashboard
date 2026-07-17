"""Bootstrap and spectrum capture-device enumeration regressions."""

from __future__ import annotations

from pathlib import Path

from app.factory import create_app
from services import spectrum_service as spectrum


STATIC = Path(__file__).resolve().parents[1] / "static"
DEVICE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}


def _approve_device(client, device_id: str = DEVICE_ID) -> dict[str, str]:
    client.post(
        "/api/device/session",
        json={"device_id": device_id, "page": "dashboard", "display_name": "boot-test"},
        headers={"Origin": "http://localhost"},
        environ_base=LOOPBACK,
    )
    client.patch(
        f"/api/settings/devices/{device_id}",
        json={"status": "approved", "workspace_id": "main"},
        headers={"Origin": "http://localhost"},
        environ_base=LOOPBACK,
    )
    return {"X-Dashboard-Device": device_id}


def test_dashboard_bootstrap_path_requires_approved_device_before_workspace():
    app = create_app({"TESTING": True})
    client = app.test_client()

    # Unregistered terminal cannot read the workspace used during bootstrap.
    blocked = client.get("/api/workspaces/main")
    assert blocked.status_code == 403
    assert blocked.get_json()["error"]["code"] == "device_required"

    # Pending terminal still cannot bootstrap the real dashboard payload.
    pending = client.post(
        "/api/device/session",
        json={"device_id": DEVICE_ID, "page": "dashboard"},
        headers={"Origin": "http://localhost"},
        environ_base=LOOPBACK,
    )
    assert pending.status_code == 200
    assert pending.get_json()["approved"] is False
    still_blocked = client.get(
        "/api/workspaces/main",
        headers={"X-Dashboard-Device": DEVICE_ID},
    )
    assert still_blocked.status_code == 403

    # Approved terminal can complete the bootstrap data path.
    headers = _approve_device(client)
    ok = client.get("/api/workspaces/main", headers=headers)
    assert ok.status_code == 200
    payload = ok.get_json()
    assert payload["id"] == "main"
    assert payload["version"] == 3
    assert payload["widgets"]
    data = client.get("/api/data", headers=headers)
    assert data.status_code == 200


def test_dashboard_bootstrap_static_order_handshakes_before_workspace_mount():
    main = (STATIC / "modules" / "dashboard" / "main.js").read_text(encoding="utf-8")
    device = (STATIC / "modules" / "dashboard" / "device.js").read_text(encoding="utf-8")
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")

    assert 'id="workspaceHost"' in html
    assert "export async function bootstrapDashboard" in main
    handshake_at = main.index("handshakeDevice(secureFetch")
    gate_at = main.index("showDeviceGate(root")
    mount_at = main.index("createWorkspaceHost({")
    ws_at = main.index("startWebSocket(")
    assert handshake_at < gate_at < mount_at < ws_at
    assert "approvedWorkspaceId = String(session.workspace_id" in main
    assert "/api/device/session" in device
    assert "cuckoo.dashboard.device_id" in device


def test_music_capture_devices_route_enumerates_loopbacks_and_current_status():
    app = create_app({"TESTING": True})
    client = app.test_client()

    remote_denied = client.get(
        "/api/music/capture-devices",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    assert remote_denied.status_code == 403

    response = client.get("/api/music/capture-devices", environ_base=LOOPBACK)
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    payload = response.get_json()
    devices = payload["devices"]
    assert devices
    assert devices[0]["id"] == "auto"
    loopbacks = [item for item in devices if item.get("kind") == "loopback"]
    assert payload["loopback_count"] == len(loopbacks)
    assert loopbacks, "expected loopback candidates in capture device list"
    assert "current" in payload
    assert "status" in payload
    assert "has_audio_stack" in payload["status"]
    assert payload["status"]["has_audio_stack"] is bool(spectrum._HAS_AUDIO)

    # Prefer soundcard loopbacks when the ole32-compatible path is available.
    if spectrum._HAS_SOUNDCARD:
        assert any(item.get("backend") == "soundcard" for item in loopbacks)
        recommended = next(item for item in loopbacks if item.get("recommended"))
        assert recommended["backend"] == "soundcard"
        assert "Loopback" in recommended["label"]


def test_music_capture_devices_refresh_route_restarts_and_returns_devices():
    app = create_app({"TESTING": True})
    client = app.test_client()
    response = client.post(
        "/api/music/capture-devices/refresh",
        json={"advanced": False},
        headers={"Origin": "http://localhost"},
        environ_base=LOOPBACK,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["devices"]
    assert payload["devices"][0]["id"] == "auto"
    assert payload["loopback_count"] >= 1
    assert "status" in payload


def test_settings_music_panel_uses_capture_device_enumeration_api():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    module = (STATIC / "settings" / "modules" / "music-settings.js").read_text(encoding="utf-8")
    assert 'id="musicCaptureDevice"' in html
    assert 'id="musicRefreshDevices"' in html
    assert "requestJson('/api/music/capture-devices')" in module
    assert "fillCaptureDeviceSelect" in module
    assert "kind === 'loopback'" in module
    assert "data.loopback_count" in module
