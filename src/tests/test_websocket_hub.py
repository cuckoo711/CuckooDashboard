"""WebSocket hub state and cleanup tests."""

from __future__ import annotations

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
