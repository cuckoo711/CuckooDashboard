"""Settings TypedDict 与动态 Provider panel 契约。"""

from __future__ import annotations

from typing import get_type_hints

from contracts.settings import ProviderPanel, SettingsOptions, SettingsPayload, SettingsSaveResult
from features.settings import schema, service


def test_settings_service_exposes_typed_return_contracts():
    assert get_type_hints(service.get_settings_options)["return"] is SettingsOptions
    assert get_type_hints(service.get_settings_payload)["return"] is SettingsPayload
    assert get_type_hints(service.save_settings_payload)["return"] is SettingsSaveResult


def test_settings_contracts_do_not_fix_provider_specific_values():
    assert "values" in ProviderPanel.__annotations__
    contract_keys = {
        *ProviderPanel.__annotations__,
        *SettingsOptions.__annotations__,
        *SettingsPayload.__annotations__,
    }
    assert not {"mimo", "nug", "local_platform"} & contract_keys


def test_provider_panel_keeps_dynamic_values_and_status_extensions(monkeypatch):
    provider = type(
        "Provider",
        (),
        {"get_status": lambda self: {"status": "ok", "ok": True, "quota_tier": "pro"}},
    )()
    provider_schema = {
        "provider": "atlas",
        "config_key": "atlas",
        "title": "Atlas",
        "description": "dynamic",
        "order": 7,
        "fields": [{"key": "region", "type": "string", "default": "global"}],
    }
    monkeypatch.setattr(schema, "get_providers", lambda: {"atlas": provider})
    monkeypatch.setattr(schema, "get_provider_config_schemas", lambda: [provider_schema])
    monkeypatch.setattr(schema, "get_provider_config", lambda name, default=None: {"region": "moon"})

    panel = schema.provider_panels({"providers": {"atlas": {"region": "moon"}}})[0]

    assert panel["values"] == {"region": "moon"}
    assert panel["status"]["quota_tier"] == "pro"
    assert schema.secret_view("secret") == {"configured": True, "masked": "••••••"}
