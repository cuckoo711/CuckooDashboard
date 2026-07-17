"""WebSocket facade compatibility and broker integration tests."""

from __future__ import annotations

import json
from typing import Any

from contracts.workspace import DataSourceDescriptor
from runtime import websocket as websocket_module
from runtime.client_session import ClientSession
from runtime.websocket import WebSocketHub, _clamp_spectrum_fps, _dashboard_media_payload
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import CORE_OWNER_ID, RegistryOwner, WorkspaceRegistry


class _Socket:
    connected = True

    def __init__(self):
        self.messages: list[dict[str, Any]] = []
        self.fail = False
        self.close_calls = 0

    def send(self, data):
        if self.fail:
            raise OSError("closed")
        self.messages.append(json.loads(data))

    def close(self):
        self.close_calls += 1
        self.connected = False


def _definition(source_id, message_type, getter, interval=1, active_interval=None):
    return DataSourceDefinition(
        descriptor=DataSourceDescriptor(
            id=source_id,
            kind="snapshot",
            legacy_message_type=message_type,
            default_interval_seconds=interval,
            active_interval_seconds=active_interval,
        ),
        getter=getter,
    )


def _registry(definitions):
    registry = WorkspaceRegistry()
    registry.register_owner(RegistryOwner(CORE_OWNER_ID, version="1.0.0", locked=True))
    registry.register_owner(RegistryOwner("com.example.extension", version="1.0.0"))
    core_types = {"dashboard_data", "github", "media", "system"}
    for definition in definitions:
        message_type = definition.descriptor.legacy_message_type
        owner = (
            CORE_OWNER_ID
            if message_type in core_types
            else "com.example.extension"
            if message_type is None
            else "cuckoo.legacy"
        )
        registry.register_data_source(definition, owner_id=owner)
    return registry


def _dashboard_report(workspace_id="main"):
    return {
        "type": "report",
        "page": "dashboard",
        "workspace_id": workspace_id,
        "viewport": {
            "width": 1920,
            "height": 1080,
            "workspace_width": 1920,
            "workspace_height": 1080,
            "device_pixel_ratio": 1,
            "visual_viewport_scale": 1,
        },
    }


def _attach(hub, sock, *, sources=None, page="unknown", client_id="client-1"):
    hub.transport.start()
    session = ClientSession(socket=sock, client_id=client_id)
    hub.transport._sessions_by_socket[sock] = session
    hub.transport._sessions_by_id[client_id] = session
    hub._on_open(session)
    sock.messages.clear()
    if page != "unknown":
        report = {"type": "report", "page": page}
        if page == "dashboard":
            report.update(
                {
                    "workspace_id": "main",
                    "viewport": {
                        "width": 1920,
                        "height": 1080,
                        "workspace_width": 1920,
                        "workspace_height": 1080,
                        "device_pixel_ratio": 1,
                        "visual_viewport_scale": 1,
                    },
                }
            )
        hub._on_message(session, report)
    if sources is not None:
        hub._subscribe_sources(session, sources, replace=True)
    sock.messages.clear()
    return session


def test_dead_client_releases_spectrum_exactly_once(monkeypatch):
    releases = []
    monkeypatch.setattr(websocket_module, "release_spectrum", lambda: releases.append(True))
    hub = WebSocketHub()
    sock = _Socket()
    session = _attach(hub, sock)
    session.spectrum = True
    sock.fail = True

    hub.broadcast({"type": "test"})
    hub.broadcast({"type": "test-again"})

    assert releases == [True]
    assert hub.list_clients() == []


def test_hub_workers_can_start_stop_and_restart():
    hub = WebSocketHub()
    assert hub.start() is True
    assert hub.start() is False
    assert hub.health()["running"] is True
    hub.stop(timeout=2)
    assert hub.health()["running"] is False
    assert hub.start() is True
    hub.stop(timeout=2)


def test_transport_compatibility_helpers_keep_existing_shapes():
    assert _clamp_spectrum_fps(1) == 12
    assert _clamp_spectrum_fps(120) == 60
    assert _clamp_spectrum_fps("bad") == 24

    payload = _dashboard_media_payload(
        {"title": "Song", "lyrics": [[0, "line"]], "cover_palette": {"x": 1}}
    )
    assert payload["title"] == "Song"
    assert payload["media_slim"] is True
    assert payload["lyrics"] == []
    assert payload["lyrics_yrc"] == []
    assert "cover_palette" not in payload


def test_connection_open_no_longer_activates_all_ordinary_sources():
    calls = []
    registry = _registry([
        _definition("system.snapshot", "system", lambda: calls.append(True) or {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    session = _attach(hub, sock)

    assert session.source_subscriptions is None
    assert hub.subscription_broker.health()["subscriptions"] == 0
    assert calls == []


def test_legacy_init_keeps_wire_shape_and_order(monkeypatch):
    calls = []
    registry = _registry([
        _definition("dashboard.aggregate", "dashboard_data", lambda: calls.append("dashboard") or {"dashboard": True}),
        _definition("github.contributions", "github", lambda: calls.append("github") or {"github": True}),
        _definition("media.playback", "media", lambda: calls.append("media") or {"media": True}),
        _definition("system.snapshot", "system", lambda: calls.append("system") or {"system": True}),
    ])
    monkeypatch.setattr(websocket_module, "_load_vibe_state", lambda: False)
    monkeypatch.setattr(websocket_module, "load_theme_index", lambda: {})
    monkeypatch.setattr(websocket_module, "theme_response", lambda _index: {"theme": True})
    monkeypatch.setattr(websocket_module, "get_font_payload", lambda: {"font": True})
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    session = _attach(hub, sock)

    hub._send_all_data(session)

    assert calls == ["dashboard", "github", "media", "system"]
    assert [message["type"] for message in sock.messages] == [
        "vibe_state",
        "dashboard_data",
        "github",
        "media",
        "system",
        "theme",
        "font",
    ]


def test_explicit_initial_push_only_fetches_selected_sources(monkeypatch):
    calls = []
    registry = _registry([
        _definition("system.snapshot", "system", lambda: calls.append("system") or {"cpu": 1}),
        _definition("github.contributions", "github", lambda: calls.append("github") or {"days": []}),
    ])
    monkeypatch.setattr(websocket_module, "_load_vibe_state", lambda: False)
    monkeypatch.setattr(websocket_module, "load_theme_index", lambda: {})
    monkeypatch.setattr(websocket_module, "theme_response", lambda _index: {})
    monkeypatch.setattr(websocket_module, "get_font_payload", lambda: {})
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    session = _attach(hub, sock, sources={"system.snapshot"})

    hub._send_all_data(session)

    assert calls == ["system"]
    assert [message["type"] for message in sock.messages] == [
        "vibe_state",
        "system",
        "theme",
        "font",
    ]


def test_source_subscribe_replace_add_unsubscribe_and_unknown_are_nonfatal():
    registry = _registry([
        _definition("system.snapshot", "system", lambda: {}),
        _definition("github.contributions", "github", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    session = _attach(hub, sock)

    hub._on_message(session, {"type": "subscribe", "sources": ["system.snapshot"], "replace": True})
    assert session.source_subscriptions == {"system.snapshot"}
    hub._on_message(session, {"type": "subscribe", "sources": ["github.contributions"], "replace": False})
    assert session.source_subscriptions == {"system.snapshot", "github.contributions"}
    hub._on_message(session, {"type": "unsubscribe", "sources": ["system.snapshot"]})
    assert session.source_subscriptions == {"github.contributions"}
    hub._on_message(session, {"type": "subscribe", "sources": ["missing.source"], "replace": False})
    hub._on_message(session, {"type": "unsubscribe", "sources": ["missing.source"]})
    assert session.source_subscriptions == {"github.contributions"}
    assert hub.transport.get_session(session.client_id) is session


def test_card_subscriptions_accept_browser_camel_case_and_split_lyric_channel(monkeypatch):
    registry = _registry([
        _definition("system.snapshot", "system", lambda: {"cpu": 1}),
    ])
    monkeypatch.setattr(websocket_module, "get_lyric_frame", lambda: {"lyric": "line"})
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    session = _attach(hub, sock)

    hub._on_message(
        session,
        {
            "type": "subscribe",
            "replace": True,
            "subscriptions": [
                {"id": "system-card", "channel": "system.snapshot", "deliveryIntervalMs": 1000},
                {"id": "lyric-card", "channel": "media.lyric"},
            ],
        },
    )
    hub._on_message(session, _dashboard_report())
    hub._on_message(session, {"type": "init"})

    assert session.wire_mode == "snapshot"
    assert session.source_subscriptions == {"system.snapshot"}
    assert session.lyric is True
    snapshots = [message for message in sock.messages if message["type"] == "data.snapshot"]
    assert snapshots[-1]["subscriptionId"] == "system-card"
    assert snapshots[-1]["channel"] == "system.snapshot"
    assert any(message["type"] == "lyric" for message in sock.messages)


def test_explicit_dashboard_sources_do_not_force_optional_lyric_channel(monkeypatch):
    monkeypatch.setattr(websocket_module, "get_lyric_frame", lambda: {"lyric": "line"})
    registry = _registry([_definition("system.snapshot", "system", lambda: {})])
    hub = WebSocketHub(workspace_registry=registry)
    modular = _attach(hub, _Socket(), client_id="modular")
    legacy = _attach(hub, _Socket(), client_id="legacy")
    music = _attach(hub, _Socket(), client_id="music")

    hub._on_message(modular, {"type": "subscribe", "sources": ["system.snapshot"], "replace": True})
    hub._on_message(modular, _dashboard_report())
    hub._on_message(legacy, _dashboard_report())
    hub._on_message(music, {"type": "report", "page": "music"})

    assert modular.lyric is False
    assert legacy.lyric is True
    assert music.lyric is True
    assert hub._lyric_interest_count() == 2


def test_explicit_lyric_unsubscribe_overrides_dashboard_page_fallback(monkeypatch):
    monkeypatch.setattr(websocket_module, "get_lyric_frame", lambda: {})
    hub = WebSocketHub()
    sock = _Socket()
    session = _attach(hub, sock, page="dashboard")
    sock.messages.clear()

    hub._on_message(session, {"type": "subscribe", "channel": "lyric", "active": False})

    assert session.lyric_explicit is True
    assert hub._lyric_interest_count() == 0
    assert hub.broadcast_lyric({"type": "lyric", "data": {"lyric": "hidden"}}) == 0
    assert sock.messages == []


def test_explicit_subscriptions_filter_legacy_message_types():
    registry = _registry([
        _definition("system.snapshot", "system", lambda: {}),
        _definition("github.contributions", "github", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    legacy_sock, system_sock, github_sock = _Socket(), _Socket(), _Socket()
    legacy = _attach(hub, legacy_sock, client_id="legacy")
    system = _attach(hub, system_sock, sources={"system.snapshot"}, client_id="system")
    github = _attach(hub, github_sock, sources={"github.contributions"}, client_id="github")
    hub._ensure_legacy_all(legacy)

    hub.broadcast({"type": "system", "data": {"cpu": 1}})
    hub.broadcast({"type": "github", "data": {"days": []}})
    hub.broadcast({"type": "theme", "data": {"name": "default"}})

    assert [message["type"] for message in legacy_sock.messages] == ["system", "github", "theme"]
    assert [message["type"] for message in system_sock.messages] == ["system", "theme"]
    assert [message["type"] for message in github_sock.messages] == ["github", "theme"]


def test_due_source_getter_runs_once_then_fans_out_to_matching_clients():
    calls = []
    registry = _registry([
        _definition("system.snapshot", "system", lambda: calls.append(True) or {"cpu": len(calls)}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    first_sock, second_sock = _Socket(), _Socket()
    _attach(hub, first_sock, sources={"system.snapshot"}, client_id="first")
    _attach(hub, second_sock, sources={"system.snapshot"}, client_id="second")

    hub._broadcast_due_sources()
    hub._broadcast_due_sources()

    assert calls == [True]
    assert first_sock.messages == [{"type": "system", "data": {"cpu": 1}}]
    assert second_sock.messages == [{"type": "system", "data": {"cpu": 1}}]


def test_generic_workspace_source_uses_source_id_envelope_for_legacy_clients():
    registry = _registry([
        _definition("com.example.health.snapshot", None, lambda: {"status": "ok"}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    _attach(hub, sock, sources={"com.example.health.snapshot"})

    hub._broadcast_due_sources()

    assert sock.messages == [
        {
            "type": "workspace_source",
            "source_id": "com.example.health.snapshot",
            "data": {"status": "ok"},
        }
    ]


def test_media_broadcast_keeps_dashboard_slim_music_full_and_filters_sources():
    registry = _registry([
        _definition("media.playback", "media", lambda: {}),
        _definition("system.snapshot", "system", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    dashboard_sock, music_sock, excluded_sock = _Socket(), _Socket(), _Socket()
    _attach(hub, dashboard_sock, sources={"media.playback"}, page="dashboard", client_id="dashboard")
    _attach(hub, music_sock, sources={"media.playback"}, page="music", client_id="music")
    _attach(hub, excluded_sock, sources={"system.snapshot"}, page="music", client_id="excluded")
    frame = {"title": "Song", "lyrics": [[0, "line"]], "cover_palette": {"accent": "red"}}

    hub.broadcast_media(frame, source_id="media.playback")

    dashboard_data = dashboard_sock.messages[0]["data"]
    music_data = music_sock.messages[0]["data"]
    assert dashboard_data["media_slim"] is True
    assert dashboard_data["lyrics"] == []
    assert "cover_palette" not in dashboard_data
    assert music_data == frame
    assert excluded_sock.messages == []


def test_dashboard_report_rejects_missing_or_invalid_viewport_without_mutating_session():
    hub = WebSocketHub()
    sock = _Socket()
    session = _attach(hub, sock)
    sock.messages.clear()

    hub._on_message(session, {"type": "report", "page": "dashboard"})

    assert session.page == "unknown"
    assert session.workspace_id is None
    assert session.workspace_width is None
    assert sock.messages == [
        {
            "type": "protocol_error",
            "error": {
                "code": "invalid_viewport",
                "message": "viewport must be an object",
                "retryable": False,
            },
        }
    ]


def test_dashboard_report_tracks_workspace_and_keeps_legacy_main_default():
    hub = WebSocketHub()
    legacy = _attach(hub, _Socket(), client_id="legacy")
    custom = _attach(hub, _Socket(), client_id="custom")
    music = _attach(hub, _Socket(), client_id="music")

    hub._on_message(legacy, _dashboard_report())
    hub._on_message(custom, _dashboard_report("ws_demo"))
    hub._on_message(music, {"type": "report", "page": "music"})

    clients = {item["id"]: item for item in hub.list_clients()}
    assert clients["legacy"]["workspace_id"] == "main"
    assert clients["custom"]["workspace_id"] == "ws_demo"
    assert clients["custom"]["workspace_width"] == 1920
    assert clients["custom"]["workspace_height"] == 1080
    assert clients["custom"]["device_pixel_ratio"] == 1
    assert clients["music"]["workspace_id"] is None
    assert hub.workspace_client_ids("main") == ["legacy"]
    assert hub.workspace_client_ids("ws_demo") == ["custom"]


def test_workspace_navigation_extends_legacy_dashboard_and_music_messages():
    hub = WebSocketHub()
    sock = _Socket()
    _attach(hub, sock)

    assert hub.navigate_client("client-1", "dashboard") is True
    assert sock.messages[-1] == {
        "type": "navigate",
        "page": "dashboard",
        "url": "/",
        "workspace_id": "main",
    }
    assert hub.navigate_client("client-1", workspace_id="ws demo") is True
    assert sock.messages[-1] == {
        "type": "navigate",
        "page": "dashboard",
        "url": "/workspaces/ws%20demo",
        "workspace_id": "ws demo",
    }
    assert hub.navigate_client("client-1", "music") is True
    assert sock.messages[-1] == {"type": "navigate", "page": "music", "url": "/music"}
    assert hub.navigate_client("missing", workspace_id="main") is False
