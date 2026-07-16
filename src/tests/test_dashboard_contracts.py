"""Dashboard aggregate 的内部快照与公开 wire 契约。"""

from __future__ import annotations

from contracts.dashboard import DashboardAggregate
from services.dashboard_data_service import build_dashboard_aggregate, build_dashboard_data


class _Provider:
    CAPABILITIES = ["daily_usage", "token_plan"]

    def get_status(self):
        return {"status": "ok", "ok": True, "vendor": "atlas"}

    def get_plan_usage(self):
        return {"items": [{"name": "daily"}]}

    def get_model_breakdown(self):
        return []

    def get_today_usage(self):
        return {
            "input_tokens": 10,
            "output_tokens": 3,
            "cached_input_tokens": 4,
            "uncached_input_tokens": 0,
            "total_tokens": 42,
            "source_count": 1,
            "period": "today",
        }


def test_dashboard_aggregate_holds_snapshots_but_public_payload_omits_them():
    aggregate = build_dashboard_aggregate(providers={"atlas": _Provider()})

    assert isinstance(aggregate, DashboardAggregate)
    assert aggregate.today.to_payload() == {
        "in": 10,
        "out": 3,
        "cache": 4,
        "total": 42,
        "inMiss": 0,
    }
    assert aggregate.snapshots["atlas"]["get_today_usage"]["total_tokens"] == 42
    public = aggregate.to_public_payload()
    assert "_provider_snapshots" not in public
    assert public["provider_statuses"]["atlas"]["vendor"] == "atlas"

    compat = aggregate.to_compat_payload()
    assert compat["_provider_snapshots"] == aggregate.snapshots


def test_legacy_dashboard_builder_keeps_private_snapshot_wire_key():
    payload = build_dashboard_data(providers={"atlas": _Provider()})

    assert set(payload["today"]) == {"in", "out", "cache", "total", "inMiss"}
    assert payload["today"]["total"] == 42
    assert "_provider_snapshots" in payload
