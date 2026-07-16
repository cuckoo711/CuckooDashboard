"""Tests for dynamically discovered Provider health entries."""

from __future__ import annotations

from services import health_service


class HealthyProvider:
    def get_status(self):
        return {"status": "ok", "enabled": True, "last_success_at": "now"}


class BrokenProvider:
    def get_status(self):
        raise RuntimeError("unavailable")


class MissingStatusProvider:
    pass



def test_health_normalization_keeps_existing_seven_key_semantics():
    assert health_service._normalize_status({"status": "stale", "details": ["lagging"]}) == {
        "status": "stale",
        "ok": False,
        "enabled": True,
        "stale": True,
        "error": None,
        "last_success_at": None,
        "details": ["lagging"],
    }



def test_health_snapshot_discovers_provider_statuses(monkeypatch):

    monkeypatch.setattr(health_service, "get_providers", lambda: {
        "zenith": HealthyProvider(),
        "archive": BrokenProvider(),
        "no-status": MissingStatusProvider(),
    })
    monkeypatch.setattr(health_service, "get_github_status", lambda: {"status": "ok"})
    monkeypatch.setattr(health_service, "get_system_status", lambda: {"status": "ok"})
    monkeypatch.setattr(health_service, "get_media_status", lambda: {"status": "ok"})

    snapshot = health_service.get_health_snapshot()

    assert list(snapshot["services"])[:3] == ["archive", "no-status", "zenith"]
    assert snapshot["services"]["zenith"]["status"] == "ok"
    assert snapshot["services"]["archive"] == {
        "status": "error",
        "ok": False,
        "enabled": True,
        "stale": False,
        "error": "unavailable",
        "last_success_at": None,
        "details": {},
    }
    assert snapshot["services"]["no-status"]["status"] == "unknown"
    assert snapshot["status"] == "error"
