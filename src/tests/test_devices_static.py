"""Static contracts for display-terminal approval flow."""

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_dashboard_bootstraps_device_handshake_and_gate():
    main = (STATIC / "modules" / "dashboard" / "main.js").read_text(encoding="utf-8")
    device = (STATIC / "modules" / "dashboard" / "device.js").read_text(encoding="utf-8")
    shared = (STATIC / "modules" / "shared" / "device-id.js").read_text(encoding="utf-8")
    http = (STATIC / "modules" / "shared" / "http.js").read_text(encoding="utf-8")
    ws = (STATIC / "modules" / "dashboard" / "ws.js").read_text(encoding="utf-8")
    assert "handshakeDevice" in main
    assert "showDeviceGate" in main
    assert "cuckoo.dashboard.device_id" in shared
    assert "from '../shared/device-id.js'" in device
    assert "X-Dashboard-Device" in http
    assert "device_id: getDeviceId()" in ws


def test_settings_page_contains_display_terminal_manager():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    main = (STATIC / "settings" / "modules" / "main.js").read_text(encoding="utf-8")
    devices = (STATIC / "settings" / "modules" / "devices.js").read_text(encoding="utf-8")
    clients = (STATIC / "settings" / "modules" / "clients.js").read_text(encoding="utf-8")
    assert 'id="devicesPanel"' in html
    assert 'id="devicesList"' in html
    assert "bindDeviceEvents" in main
    assert "/api/settings/devices" in devices
    assert "device-approve-btn" in devices
    assert "device-delete-btn" in devices
    assert "method: 'DELETE'" in devices
    assert "startClientsAutoRefresh" in clients
    assert "CLIENTS_AUTO_REFRESH_MS" in clients
