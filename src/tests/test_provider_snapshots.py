"""Tests for generic Provider snapshot handoff to Vibe data."""

from __future__ import annotations

import providers


def test_provider_snapshots_use_runtime_registration_name(monkeypatch):
    provider = object()
    methods = {
        "get_plan_usage": {"items": []},
        "get_balance": {"balance": "1"},
    }
    monkeypatch.setattr(providers, "get_providers", lambda: {"third-party-source": provider})

    snapshot = providers._provider_snapshots(provider, methods)

    assert snapshot == {"third-party-source": methods}
