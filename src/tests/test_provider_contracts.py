"""Provider 类型化契约与兼容调用行为。"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

import providers as provider_registry
from contracts.provider import DailyUsage, ProviderStatus


def test_provider_status_preserves_extensions_and_builds_health_shape():
    raw = {
        "status": "ok",
        "ok": True,
        "enabled": True,
        "error": None,
        "last_success_at": "now",
        "account_count": 3,
        "vendor_state": {"mode": "ready"},
    }
    status = ProviderStatus.from_value(raw)

    assert status.to_provider_payload() == raw
    assert status.extensions == {
        "account_count": 3,
        "vendor_state": {"mode": "ready"},
    }
    assert status.to_health_payload() == {
        "status": "ok",
        "ok": True,
        "enabled": True,
        "stale": False,
        "error": None,
        "last_success_at": "now",
        "details": {},
    }
    assert ProviderStatus.from_value({}).to_provider_payload() == {}


def test_daily_usage_normalizes_counts_without_recomputing_total():
    usage = DailyUsage.from_value({
        "input_tokens": "10",
        "output_tokens": -2,
        "cached_input_tokens": 4,
        "total_tokens": 99,
        "source_count": "2",
    })
    assert usage.to_payload() == {
        "input_tokens": 10,
        "output_tokens": 0,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 6,
        "total_tokens": 99,
        "source_count": 2,
        "period": "today",
    }

    explicit_zero = DailyUsage.from_value({
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 0,
        "total_tokens": 1,
    })
    assert explicit_zero.uncached_input_tokens == 0
    assert explicit_zero.total_tokens == 1


def test_call_all_and_call_one_keep_legacy_return_shapes(monkeypatch):
    good = ModuleType("good")
    good.read = lambda: {"value": 1}
    broken = ModuleType("broken")

    def fail():
        raise RuntimeError("boom")

    broken.read = fail
    missing = ModuleType("missing")
    providers = {"good": good, "broken": broken, "missing": missing}
    monkeypatch.setattr(provider_registry, "get_providers_by_capability", lambda capability: providers)
    monkeypatch.setattr(provider_registry, "get_provider", lambda provider_id: providers.get(provider_id))

    assert provider_registry.call_all("sample", "read") == [
        {"provider": "broken", "data": None, "error": "boom"},
        {"provider": "good", "data": {"value": 1}},
    ]
    assert provider_registry.call_one("good", "read") == {"value": 1}
    assert provider_registry.call_one("broken", "read") is None
    assert provider_registry.call_one("missing", "read") is None


def test_discovery_warns_but_registers_incomplete_third_party_plugin(
    monkeypatch, tmp_path, caplog
):
    package = tmp_path / "third_party"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = ModuleType("providers.third_party")
    module.PROVIDER_ID = "third_party"
    module.CAPABILITIES = ["daily_usage"]
    module.get_status = lambda: {"status": "ok", "ok": True}

    monkeypatch.setattr(provider_registry, "_PROVIDERS_DIR", tmp_path)
    monkeypatch.setattr(provider_registry, "_registry", {})
    monkeypatch.setattr(provider_registry, "_discovered", False)
    monkeypatch.setattr(provider_registry.importlib, "import_module", lambda name: module)
    monkeypatch.setattr(provider_registry.refresh_scheduler, "register_provider", lambda *args: None)

    with caplog.at_level(logging.WARNING):
        discovered = provider_registry.get_providers()

    assert discovered == {"third_party": module}
    assert "capability-method 不完整" in caplog.text
    assert "仍继续加载" in caplog.text


def test_builtin_provider_capability_methods_are_complete():
    for provider_id in ("mimo", "nug", "local_platform"):
        provider = importlib.import_module(f"providers.{provider_id}")
        assert callable(getattr(provider, "get_status", None))
        for capability in provider.CAPABILITIES:
            for method in provider_registry.CAPABILITY_METHODS[capability]:
                assert callable(getattr(provider, method, None)), (
                    provider_id,
                    capability,
                    method,
                )
