"""Application factory and per-app routing tests."""

from __future__ import annotations

import threading

from app.factory import create_app


def _routes(app):
    return {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
    }


def test_factory_creates_isolated_apps_with_provider_routes():
    first = create_app({"TESTING": True})
    second = create_app({"TESTING": True})

    assert first is not second
    assert first.extensions["dashboard_runtime"] is not second.extensions["dashboard_runtime"]
    assert first.extensions["workspace_registry"] is first.extensions["dashboard_runtime"].workspace_registry
    assert second.extensions["workspace_registry"] is second.extensions["dashboard_runtime"].workspace_registry
    assert first.extensions["workspace_registry"] is not second.extensions["workspace_registry"]
    assert first.extensions["extension_manager"] is not second.extensions["extension_manager"]
    assert first.extensions["extension_state_repository"] is not second.extensions["extension_state_repository"]
    assert first.extensions["source_cache"] is first.extensions["dashboard_runtime"].source_cache
    assert first.extensions["refresh_scheduler"] is first.extensions["dashboard_runtime"].refresh_scheduler
    assert first.extensions["subscription_broker"] is first.extensions["dashboard_runtime"].subscription_broker
    assert first.extensions["websocket_transport"] is first.extensions["dashboard_runtime"].websocket_transport
    assert _routes(first) == _routes(second)

    paths = {rule for rule, _ in _routes(first)}
    assert "/auth/mimo/" in paths
    assert "/auth/nug/" in paths
    assert "/auth/local_platform/" in paths


def test_factory_does_not_start_background_threads():
    before = {thread.ident for thread in threading.enumerate()}
    app = create_app({"TESTING": True})
    runtime = app.extensions["dashboard_runtime"]
    after = {thread.ident for thread in threading.enumerate()}

    assert runtime.started is False
    assert after == before
