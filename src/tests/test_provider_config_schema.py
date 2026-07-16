"""Tests for Provider configuration Schema discovery and generic rendering."""

from __future__ import annotations

import copy

import services.settings_service as settings_service
from providers import _valid_schema_fields, get_auth_providers, get_provider_config_schemas


def test_builtin_provider_schemas_are_discovered():
    schemas = get_provider_config_schemas()
    by_key = {schema["config_key"]: schema for schema in schemas}

    assert {"mimo", "local_platform", "nug"}.issubset(by_key)
    # 内置 Provider 的认证字段已迁入自定义 DPAPI Vault 认证页，不再出现在 YAML Schema。
    assert all(field["type"] != "secret" for field in by_key["nug"]["fields"])
    local_urls = next(field for field in by_key["local_platform"]["fields"] if field["key"] == "urls")
    assert local_urls["type"] == "object_list"
    assert local_urls["identity_key"] == "url"
    assert any(field["key"] == "credential_ref" for field in local_urls["item_fields"])
    assert {"mimo", "local_platform", "nug"}.issubset(get_auth_providers())


def test_invalid_schema_field_types_are_rejected():
    assert not _valid_schema_fields([{"key": "bad", "type": "unknown"}], "test")
    assert not _valid_schema_fields([{"key": "rows", "type": "object_list", "item_fields": []}], "test")


def test_fake_provider_schema_appears_without_settings_code_changes(monkeypatch):
    fake_schema = [{
        "provider": "atlas",
        "config_key": "atlas",
        "title": "Atlas",
        "description": "测试 Provider",
        "order": 1,
        "fields": [
            {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
            {"key": "api_key", "label": "API Key", "type": "secret", "default": ""},
        ],
    }]
    fake_provider = type("FakeProvider", (), {
        "get_status": lambda self: {"status": "unknown", "ok": False, "enabled": True}
    })()
    base = {
        "config_version": 4,
        "providers": {"atlas": {"enabled": True}},
        "dashboard": {},
    }
    monkeypatch.setattr(settings_service, "get_provider_config_schemas", lambda: copy.deepcopy(fake_schema))
    monkeypatch.setattr(settings_service, "get_providers", lambda: {"atlas": fake_provider})
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(settings_service, "get_provider_config", lambda name, default=None: {"enabled": True, "api_key": "atlas-secret"})
    monkeypatch.setattr(settings_service, "get_settings_options", lambda: {})

    payload = settings_service.get_settings_payload()

    assert len(payload["providers"]) == 1
    panel = payload["providers"][0]
    assert panel["config_key"] == "atlas"
    assert panel["values"]["enabled"] is True
    assert panel["values"]["api_key"]["configured"] is True
