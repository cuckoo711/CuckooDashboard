"""Managed application runtime lifecycle tests."""

from __future__ import annotations

from runtime import lifecycle as lifecycle_module
from runtime.lifecycle import DashboardRuntime
from workspaces.registry import WorkspaceRegistry


class _Hub:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self):
        changed = not self.running
        self.running = True
        self.starts += 1
        return changed

    def stop(self, timeout=5):
        self.running = False
        self.stops += 1

    def health(self):
        return {"running": self.running}


def test_runtime_uses_injected_registry_without_mutating_injected_websocket():
    hub = _Hub()
    registry = WorkspaceRegistry()

    runtime = DashboardRuntime(websocket=hub, workspace_registry=registry)

    assert runtime.workspace_registry is registry
    assert runtime.websocket is hub
    assert not hasattr(hub, "workspace_registry")


class _Scheduler:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self.running = True
        self.starts += 1

    def stop(self, timeout=5):
        self.running = False
        self.stops += 1

    def health(self):
        return {"running": self.running}


def test_runtime_start_stop_is_idempotent_and_runs_shutdown_hooks(monkeypatch):
    stopped = []
    monkeypatch.setattr(lifecycle_module, "shutdown_spectrum", lambda timeout=5: stopped.append("spectrum"))
    monkeypatch.setattr(lifecycle_module, "stop_media_service", lambda timeout=5: stopped.append("media"))
    monkeypatch.setattr(lifecycle_module, "stop_system_service", lambda timeout=5: stopped.append("system"))

    hub = _Hub()
    scheduler = _Scheduler()
    runtime = DashboardRuntime(websocket=hub, auth_scheduler=scheduler)

    assert runtime.start() is True
    assert runtime.start() is False
    assert runtime.started is True
    runtime.stop(timeout=0)
    runtime.stop(timeout=0)

    assert runtime.started is False
    assert hub.starts == 2
    assert hub.stops == 2
    assert scheduler.starts == 2
    assert scheduler.stops == 2
    assert stopped == ["spectrum", "media", "system"] * 2


def test_runtime_orders_extension_lifecycle_around_websocket(monkeypatch):
    events = []
    monkeypatch.setattr(lifecycle_module, "shutdown_spectrum", lambda timeout=5: None)
    monkeypatch.setattr(lifecycle_module, "stop_media_service", lambda timeout=5: None)
    monkeypatch.setattr(lifecycle_module, "stop_system_service", lambda timeout=5: None)

    class Hub(_Hub):
        def start(self):
            events.append("websocket:start")
            return super().start()

        def stop(self, timeout=5):
            events.append("websocket:stop")
            return super().stop(timeout)

    class Scheduler(_Scheduler):
        def start(self):
            events.append("auth:start")
            super().start()

        def stop(self, timeout=5):
            events.append("auth:stop")
            super().stop(timeout)

    class SourceScheduler(_Scheduler):
        def start(self):
            events.append("refresh:start")
            super().start()
            return True

        def stop(self, timeout=5):
            events.append("refresh:stop")
            super().stop(timeout)

    class StateRepository:
        def close(self):
            events.append("extensions:repository-close")

    class Extensions:
        state_repository = StateRepository()

        def is_owner_available(self, _owner):
            return True

        def owner_allows_new_widgets(self, _owner):
            return True

        def owner_unavailable_reason(self, _owner):
            return None

        def start_all(self, _runtime):
            events.append("extensions:start")

        def stop_all(self, _runtime, timeout=5):
            events.append("extensions:stop")

        def health(self):
            return {"effective": 0}

    runtime = DashboardRuntime(
        websocket=Hub(),
        auth_scheduler=Scheduler(),
        refresh_scheduler=SourceScheduler(),
        extension_manager=Extensions(),
    )
    runtime.start()
    runtime.stop(timeout=0)

    assert events[:4] == [
        "extensions:start",
        "refresh:start",
        "websocket:start",
        "auth:start",
    ]
    assert events[4:8] == [
        "websocket:stop",
        "refresh:stop",
        "auth:stop",
        "extensions:stop",
    ]
    assert events[-1] == "extensions:repository-close"
