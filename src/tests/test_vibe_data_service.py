"""Tests for capability-based Vibe Coding data sources."""

from __future__ import annotations

from services.vibe_data_service import build_vibe_coding_config, get_vibe_data


class FakeProvider:
    def __init__(
        self,
        capabilities=(),
        *,
        usage=None,
        models=None,
        channels=None,
        balance=None,
    ):
        self.CAPABILITIES = list(capabilities)
        self.usage = usage
        self.models = models
        self.channels = channels
        self.balance = balance
        self.calls: list[str] = []

    def get_plan_usage(self):
        self.calls.append("get_plan_usage")
        return self.usage

    def get_model_breakdown(self):
        self.calls.append("get_model_breakdown")
        return self.models

    def get_channel_breakdown(self, days=7):
        self.calls.append(f"get_channel_breakdown:{days}")
        return self.channels

    def get_balance(self):
        self.calls.append("get_balance")
        return self.balance


def token_usage(*items):
    return {"monthUsage": {"items": list(items)}}


def test_ring_defaults_to_stable_first_provider_and_plan_item():
    alpha = FakeProvider(
        ["token_plan"],
        usage=token_usage(
            {"id": "z-plan", "name": "Z Plan", "used": 20, "limit": 100},
            {"id": "a-plan", "name": "A Plan", "used": 25, "limit": 100},
        ),
    )
    zeta = FakeProvider(
        ["token_plan"],
        usage=token_usage({"id": "only", "name": "Only", "used": 1, "limit": 10}),
    )

    result = get_vibe_data(config={}, providers={"zeta": zeta, "alpha": alpha})

    assert result["ring"] == {
        "available": True,
        "provider": "alpha",
        "item": "A Plan",
        "percent": 25.0,
        "used": 25.0,
        "limit": 100.0,
    }


def test_ring_explicit_provider_and_item_match_plan_code():
    atlas = FakeProvider(
        ["token_plan"],
        usage=token_usage(
            {"planCode": "basic", "name": "Basic", "used": 1, "limit": 10},
            {"planCode": "pro", "name": "Pro", "used": 9, "limit": 20},
        ),
    )
    config = {
        "dashboard": {
            "vibe_coding": {
                "ring": {"provider": "atlas", "item": "pro"},
            },
        },
    }

    result = get_vibe_data(config=config, providers={"atlas": atlas})

    assert result["ring"]["provider"] == "atlas"
    assert result["ring"]["item"] == "Pro"
    assert result["ring"]["percent"] == 45.0


def test_model_bar_source_can_switch_between_models_and_channels():
    forge = FakeProvider(
        ["token_plan", "api_usage"],
        models=[{"model": "forge-model", "totalToken": 1234, "requestCount": 7}],
    )
    orbit = FakeProvider(
        ["api_usage"],
        channels=[{
            "groupKey": "orbit-channel",
            "cost": "4.25",
            "currency": "EUR",
            "requestCount": 9,
        }],
    )

    forge_result = get_vibe_data(
        config={"dashboard": {"vibe_coding": {"model_bars": {"provider": "forge"}}}},
        providers={"orbit": orbit, "forge": forge},
    )
    orbit_result = get_vibe_data(
        config={"dashboard": {"vibe_coding": {"model_bars": {"provider": "orbit"}}}},
        providers={"orbit": orbit, "forge": forge},
    )

    assert forge_result["model_bars"] == {
        "available": True,
        "provider": "forge",
        "kind": "tokens",
        "currency": None,
        "rows": [{"label": "forge-model", "value": 1234.0, "requests": 7}],
    }
    assert orbit_result["model_bars"] == {
        "available": True,
        "provider": "orbit",
        "kind": "currency",
        "currency": "EUR",
        "rows": [{"label": "orbit-channel", "value": 4.25, "requests": 9}],
    }


def test_default_model_source_uses_stable_provider_order_without_preference():
    alpha = FakeProvider(
        ["api_usage"],
        channels=[{"channel": "alpha", "tokens": 8, "requests": 2}],
    )
    zeta = FakeProvider(
        ["api_usage"],
        channels=[{"channel": "zeta", "cost": 2, "currency": "USD", "requests": 1}],
    )

    result = get_vibe_data(config={}, providers={"zeta": zeta, "alpha": alpha})

    assert result["model_bars"] == {
        "available": True,
        "provider": "alpha",
        "kind": "tokens",
        "currency": None,
        "rows": [{"label": "alpha", "value": 8.0, "requests": 2}],
    }


def test_balances_are_opt_in_sorted_capped_and_sanitized():
    alpha = FakeProvider(["balance"], balance={"balance": "1.00", "currency": "USD"})
    beta = FakeProvider(["balance"], balance={"balance": "20.50", "currency": "CNY"})
    zeta = FakeProvider(["balance"], balance={"balance": "3.25", "currency": "EUR"})
    config = {
        "dashboard": {
            "vibe_coding": {
                "balances": [
                    {"provider": "zeta", "name": "Zeta", "color": "#5fa89e", "enabled": True},
                    {"provider": "beta", "name": "Beta", "color": "not-a-color", "enabled": True},
                    {"provider": "alpha", "name": "Alpha", "color": "#123456", "enabled": True},
                    {"provider": "disabled", "name": "Hidden", "color": "#ffffff", "enabled": False},
                    {"provider": "unknown", "name": "Unknown", "color": "#ffffff", "enabled": True},
                ],
            },
        },
    }

    result = get_vibe_data(config=config, providers={"zeta": zeta, "beta": beta, "alpha": alpha})

    assert result["balances"] == [
        {
            "provider": "alpha",
            "name": "Alpha",
            "color": "#123456",
            "currency": "USD",
            "balance": "1.00",
        },
        {
            "provider": "beta",
            "name": "Beta",
            "color": "#888888",
            "currency": "CNY",
            "balance": "20.50",
        },
    ]


def test_missing_balance_configuration_keeps_footer_empty():
    ledger = FakeProvider(["balance"], balance={"balance": "20.50", "currency": "CNY"})

    result = get_vibe_data(config={"dashboard": {"vibe_coding": {}}}, providers={"ledger": ledger})

    assert result["balances"] == []


def test_prefetched_provider_snapshot_is_reused_without_provider_calls():
    atlas = FakeProvider(
        ["token_plan", "balance", "api_usage"],
        usage=AssertionError("prefetched value should be used"),
        models=AssertionError("prefetched value should be used"),
        balance=AssertionError("prefetched value should be used"),
    )
    prefetched = {
        "atlas": {
            "get_plan_usage": token_usage({"name": "Plan", "used": 2, "limit": 10}),
            "get_model_breakdown": [{"model": "atlas-model", "totalToken": 10, "requestCount": 1}],
            "get_balance": {"balance": "9.99", "currency": "CNY"},
        },
    }
    config = {
        "dashboard": {
            "vibe_coding": {
                "ring": {"provider": "atlas"},
                "model_bars": {"provider": "atlas"},
                "balances": [{"provider": "atlas", "name": "Atlas", "color": "#abcdef"}],
            },
        },
    }

    result = get_vibe_data(
        prefetched_provider_data=prefetched,
        config=config,
        providers={"atlas": atlas},
    )

    assert result["ring"]["available"] is True
    assert result["model_bars"]["available"] is True
    assert result["balances"][0]["balance"] == "9.99"
    assert atlas.calls == []


def test_config_parser_deduplicates_balance_provider_by_stable_sort_order():
    config = {
        "dashboard": {
            "vibe_coding": {
                "balances": [
                    {"provider": "ledger", "name": "Z", "color": "#000001"},
                    {"provider": "LEDGER", "name": "A", "color": "#000002"},
                ],
            },
        },
    }

    normalized = build_vibe_coding_config(config)

    assert normalized["balances"] == [{"provider": "LEDGER", "name": "A", "color": "#000002"}]
