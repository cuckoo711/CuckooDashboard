"""Tests for generic Dashboard Provider snapshots."""

from __future__ import annotations

from services.dashboard_data_service import build_dashboard_data


class _Provider:
    CAPABILITIES = ["daily_usage", "token_plan", "balance"]

    def get_status(self):
        return {"status": "ok", "ok": True, "enabled": True, "error": None}

    def get_today_usage(self):
        return {
            "input_tokens": 12,
            "output_tokens": 3,
            "cached_input_tokens": 5,
            "uncached_input_tokens": 7,
            "total_tokens": 15,
            "source_count": 1,
            "period": "today",
        }

    def get_plan_usage(self):
        return {"items": []}

    def get_balance(self):
        return {"balance": "1", "currency": "USD"}


def test_dashboard_snapshots_use_runtime_registration_name():
    data = build_dashboard_data(providers={"third-party-source": _Provider()})

    assert data["today"] == {"in": 12, "out": 3, "cache": 5, "total": 15, "inMiss": 7}
    assert data["_provider_snapshots"]["third-party-source"] == {
        "get_plan_usage": {"items": []},
        "get_balance": {"balance": "1", "currency": "USD"},
        "get_today_usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "cached_input_tokens": 5,
            "uncached_input_tokens": 7,
            "total_tokens": 15,
            "source_count": 1,
            "period": "today",
        },
    }
