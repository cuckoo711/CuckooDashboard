"""Managed application runtime lifecycle tests."""

from __future__ import annotations

from runtime import lifecycle as lifecycle_module
from runtime.lifecycle import DashboardRuntime


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
