"""Stable online-client identity uses persistent device_id."""

from __future__ import annotations

from pathlib import Path

from runtime.client_session import ClientSession


STATIC = Path(__file__).resolve().parents[1] / "static"


class _Sock:
    connected = True

    def send(self, _data):
        return None


def test_client_list_payload_prefers_stable_device_id():
    session = ClientSession(socket=_Sock(), client_id="a1b2c3d4")
    session.device_id = "11111111-1111-4111-8111-111111111111"
    session.device_status = "approved"
    session.page = "dashboard"
    session.workspace_id = "main"
    payload = session.list_payload()
    assert payload["id"] == "11111111-1111-4111-8111-111111111111"
    assert payload["session_id"] == "a1b2c3d4"
    assert payload["device_id"] == "11111111-1111-4111-8111-111111111111"


def test_settings_clients_ui_shows_device_id_and_targets_session_id():
    source = (STATIC / "settings" / "modules" / "clients.js").read_text(encoding="utf-8")
    assert "client.device_id || client.id" in source
    assert "client.session_id || client.id" in source
    assert 'data-device-id="' in source
    assert "稳定设备 ID" in source


def test_music_and_settings_report_persistent_device_id():
    music = (STATIC / "modules" / "music" / "ws.js").read_text(encoding="utf-8")
    settings = (STATIC / "settings" / "modules" / "clients.js").read_text(encoding="utf-8")
    shared = (STATIC / "modules" / "shared" / "device-id.js").read_text(encoding="utf-8")
    assert "withDeviceId" in music
    assert "page: 'music'" in music
    assert "device_id: getDeviceId()" in settings
    assert "page: 'settings'" in settings
    assert "cuckoo.dashboard.device_id" in shared


def test_settings_page_is_excluded_from_online_board_lists():
    from runtime.websocket import WebSocketHub

    class _Sock:
        connected = True

        def send(self, _data):
            return None

    hub = WebSocketHub()
    settings = ClientSession(socket=_Sock(), client_id="s1")
    settings.page = "settings"
    settings.device_id = "11111111-1111-4111-8111-111111111111"
    music = ClientSession(socket=_Sock(), client_id="m1")
    music.page = "music"
    music.device_id = "22222222-2222-4222-8222-222222222222"
    dashboard = ClientSession(socket=_Sock(), client_id="d1")
    dashboard.page = "dashboard"
    dashboard.device_id = "33333333-3333-4333-8333-333333333333"
    # Inject directly into the transport session index used by list_clients().
    hub.transport._sessions_by_id = {
        settings.client_id: settings,
        music.client_id: music,
        dashboard.client_id: dashboard,
    }
    listed = hub.list_clients()
    pages = {item["page"] for item in listed}
    assert "settings" not in pages
    assert pages == {"music", "dashboard"}

    clients_ui = (STATIC / "settings" / "modules" / "clients.js").read_text(encoding="utf-8")
    assert "page !== 'settings'" in clients_ui or 'page !== "settings"' in clients_ui
