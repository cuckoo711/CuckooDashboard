"""WebSocket hub state and cleanup tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from runtime import websocket as websocket_module
from runtime.websocket import WebSocketHub, _clamp_spectrum_fps, _dashboard_media_payload


class _DeadSocket:
    connected = True

    def send(self, _data):
        raise OSError("closed")


def test_dead_client_releases_spectrum_exactly_once(monkeypatch):
    releases = []
    monkeypatch.setattr(websocket_module, "release_spectrum", lambda: releases.append(True))
    hub = WebSocketHub()
    sock = _DeadSocket()
    hub._clients.append(sock)
    hub._states[sock] = {"spectrum": True, "page": "music", "vibe": False}

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

    payload = _dashboard_media_payload({"title": "Song", "lyrics": [[0, "line"]], "cover_palette": {"x": 1}})
    assert payload["title"] == "Song"
    assert payload["media_slim"] is True
    assert payload["lyrics"] == []
    assert payload["lyrics_yrc"] == []
    assert "cover_palette" not in payload


class _Socket:
    connected = True

    def __init__(self):
        self.messages = []

    def send(self, data):
        self.messages.append(json.loads(data))


class _Registry:
    def __init__(self, definitions):
        self._definitions = {definition.descriptor.id: definition for definition in definitions}

    def get_data_source(self, source_id):
        return self._definitions[source_id]

    def data_source_ids(self):
        return tuple(self._definitions)

    def iter_data_sources(self):
        return iter(tuple(self._definitions.values()))


def _definition(source_id, message_type, getter, interval=1, active_interval=None):
    return SimpleNamespace(
        descriptor=SimpleNamespace(
            id=source_id,
            legacy_message_type=message_type,
            default_interval_seconds=interval,
            active_interval_seconds=active_interval,
        ),
        getter=getter,
    )


def _add_client(hub, sock, sources=None, page="unknown"):
    hub._clients.append(sock)
    hub._states[sock] = {
        "id": f"client-{len(hub._clients)}",
        "page": page,
        "vibe": False,
        "spectrum": False,
        "lyric": False,
        "source_subscriptions": sources,
    }


def test_legacy_initial_push_keeps_existing_wire_shape(monkeypatch):
    monkeypatch.setattr(websocket_module, "_load_vibe_state", lambda: False)
    monkeypatch.setattr(websocket_module, "get_dashboard_data", lambda: {"dashboard": True})
    monkeypatch.setattr(websocket_module, "get_github_data", lambda: {"github": True})
    monkeypatch.setattr(websocket_module, "get_media_info", lambda: {"media": True})
    monkeypatch.setattr(websocket_module, "get_system_info", lambda: {"system": True})
    monkeypatch.setattr(websocket_module, "load_theme_index", lambda: {})
    monkeypatch.setattr(websocket_module, "theme_response", lambda _index: {"theme": True})
    monkeypatch.setattr(websocket_module, "get_font_payload", lambda: {"font": True})

    hub = WebSocketHub()
    sock = _Socket()
    _add_client(hub, sock)

    hub._send_all_data(sock)

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
    registry = _Registry([
        _definition("system.snapshot", "system", lambda: calls.append("system") or {"cpu": 1}),
        _definition("github.contributions", "github", lambda: calls.append("github") or {"days": []}),
    ])
    monkeypatch.setattr(websocket_module, "_load_vibe_state", lambda: False)
    monkeypatch.setattr(websocket_module, "load_theme_index", lambda: {})
    monkeypatch.setattr(websocket_module, "theme_response", lambda _index: {})
    monkeypatch.setattr(websocket_module, "get_font_payload", lambda: {})
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    _add_client(hub, sock, sources={"system.snapshot"})

    hub._send_all_data(sock)

    assert calls == ["system"]
    assert [message["type"] for message in sock.messages] == [
        "vibe_state",
        "system",
        "theme",
        "font",
    ]


def test_source_subscribe_replace_add_unsubscribe_and_unknown_are_nonfatal():
    registry = _Registry([
        _definition("system.snapshot", "system", lambda: {}),
        _definition("github.contributions", "github", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    sock = _Socket()
    _add_client(hub, sock)

    hub._handle_message(
        sock,
        "client-1",
        json.dumps({"type": "subscribe", "sources": ["system.snapshot"], "replace": True}),
    )
    assert hub._states[sock]["source_subscriptions"] == {"system.snapshot"}

    hub._handle_message(
        sock,
        "client-1",
        json.dumps({"type": "subscribe", "sources": ["github.contributions"], "replace": False}),
    )
    assert hub._states[sock]["source_subscriptions"] == {
        "system.snapshot",
        "github.contributions",
    }

    hub._handle_message(
        sock,
        "client-1",
        json.dumps({"type": "unsubscribe", "sources": ["system.snapshot"]}),
    )
    assert hub._states[sock]["source_subscriptions"] == {"github.contributions"}

    hub._handle_message(
        sock,
        "client-1",
        json.dumps({"type": "subscribe", "sources": ["missing.source"], "replace": False}),
    )
    hub._handle_message(
        sock,
        "client-1",
        json.dumps({"type": "unsubscribe", "sources": ["missing.source"]}),
    )
    assert hub._states[sock]["source_subscriptions"] == {"github.contributions"}
    assert sock in hub._clients


def test_explicit_dashboard_sources_do_not_force_optional_lyric_channel():
    registry = _Registry([
        _definition("system.snapshot", "system", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    modular = _Socket()
    legacy = _Socket()
    music = _Socket()
    _add_client(hub, modular)
    _add_client(hub, legacy)
    _add_client(hub, music)

    hub._handle_message(
        modular,
        "client-1",
        json.dumps({"type": "subscribe", "sources": ["system.snapshot"], "replace": True}),
    )
    hub._handle_message(modular, "client-1", json.dumps({"type": "report", "page": "dashboard"}))
    hub._handle_message(legacy, "client-2", json.dumps({"type": "report", "page": "dashboard"}))
    hub._handle_message(music, "client-3", json.dumps({"type": "report", "page": "music"}))

    assert hub._states[modular]["lyric"] is False
    assert hub._states[legacy]["lyric"] is True
    assert hub._states[music]["lyric"] is True


def test_explicit_subscriptions_filter_legacy_message_types():
    registry = _Registry([
        _definition("system.snapshot", "system", lambda: {}),
        _definition("github.contributions", "github", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    legacy = _Socket()
    system = _Socket()
    github = _Socket()
    _add_client(hub, legacy)
    _add_client(hub, system, sources={"system.snapshot"})
    _add_client(hub, github, sources={"github.contributions"})

    hub.broadcast({"type": "system", "data": {"cpu": 1}})
    hub.broadcast({"type": "github", "data": {"days": []}})
    hub.broadcast({"type": "theme", "data": {"name": "default"}})

    assert [message["type"] for message in legacy.messages] == ["system", "github", "theme"]
    assert [message["type"] for message in system.messages] == ["system", "theme"]
    assert [message["type"] for message in github.messages] == ["github", "theme"]


def test_due_source_getter_runs_once_then_fans_out_to_matching_clients():
    calls = []
    registry = _Registry([
        _definition(
            "system.snapshot",
            "system",
            lambda: calls.append(True) or {"cpu": len(calls)},
        ),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    first = _Socket()
    second = _Socket()
    _add_client(hub, first, sources={"system.snapshot"})
    _add_client(hub, second, sources={"system.snapshot"})

    hub._broadcast_due_sources(now=10)
    hub._broadcast_due_sources(now=10.5)

    assert calls == [True]
    assert first.messages == [{"type": "system", "data": {"cpu": 1}}]
    assert second.messages == [{"type": "system", "data": {"cpu": 1}}]


def test_media_broadcast_keeps_dashboard_slim_music_full_and_filters_sources():
    registry = _Registry([
        _definition("media.playback", "media", lambda: {}),
        _definition("system.snapshot", "system", lambda: {}),
    ])
    hub = WebSocketHub(workspace_registry=registry)
    dashboard = _Socket()
    music = _Socket()
    excluded = _Socket()
    _add_client(hub, dashboard, page="dashboard")
    _add_client(hub, music, sources={"media.playback"}, page="music")
    _add_client(hub, excluded, sources={"system.snapshot"}, page="music")
    frame = {"title": "Song", "lyrics": [[0, "line"]], "cover_palette": {"accent": "red"}}

    hub.broadcast_media(frame, source_id="media.playback")

    dashboard_data = dashboard.messages[0]["data"]
    music_data = music.messages[0]["data"]
    assert dashboard_data["media_slim"] is True
    assert dashboard_data["lyrics"] == []
    assert "cover_palette" not in dashboard_data
    assert music_data == frame
    assert excluded.messages == []
