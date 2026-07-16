"""Guard the public route surface while routes move between Blueprints."""

from __future__ import annotations

from app.factory import create_app

_EXPECTED = {
    ("/", frozenset({"GET"})),
    ("/music", frozenset({"GET"})),
    ("/settings", frozenset({"GET"})),
    ("/settings-assets/<path:filename>", frozenset({"GET"})),
    ("/ws", frozenset({"GET"})),
    ("/api/data", frozenset({"GET"})),
    ("/api/health", frozenset({"GET"})),
    ("/api/off-peak-badge", frozenset({"GET"})),
    ("/api/system", frozenset({"GET"})),
    ("/api/workspaces/<workspace_id>", frozenset({"GET"})),
    ("/api/vibe", frozenset({"GET", "POST"})),
    ("/api/theme", frozenset({"GET", "POST"})),
    ("/api/theme/next", frozenset({"POST"})),
    ("/api/font", frozenset({"GET"})),
    ("/api/fonts", frozenset({"GET"})),
    ("/api/fonts/upload", frozenset({"POST"})),
    ("/api/fonts/delete", frozenset({"POST"})),
    ("/api/media", frozenset({"GET"})),
    ("/api/media/cover", frozenset({"GET"})),
    ("/api/media/cover/ambient", frozenset({"GET"})),
    ("/api/media/reload", frozenset({"POST"})),
    ("/api/media/offset", frozenset({"GET", "POST"})),
    ("/api/music/offset", frozenset({"GET", "POST"})),
    ("/api/music/capture-devices", frozenset({"GET"})),
    ("/api/music/capture-devices/refresh", frozenset({"POST"})),
    ("/api/music/spectrum", frozenset({"GET"})),
    ("/api/music/spectrum/status", frozenset({"GET"})),
    ("/api/music/spectrum/acquire", frozenset({"POST"})),
    ("/api/music/spectrum/release", frozenset({"POST"})),
    ("/api/music/calibrate", frozenset({"GET", "POST"})),
    ("/api/player/<action>", frozenset({"POST"})),
    ("/api/providers", frozenset({"GET"})),
    ("/api/providers/<provider_id>/<resource>", frozenset({"GET"})),
    ("/api/settings", frozenset({"GET", "POST"})),
    ("/api/settings/reload-clients", frozenset({"POST"})),
    ("/api/settings/clients", frozenset({"GET"})),
    ("/api/settings/clients/<client_id>/navigate", frozenset({"POST"})),
    ("/api/settings/clients/<client_id>/screenshot", frozenset({"POST"})),
    ("/api/settings/reveal", frozenset({"POST"})),
}


def test_host_route_surface_is_preserved():
    app = create_app({"TESTING": True})
    actual = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
    }
    assert _EXPECTED <= actual
