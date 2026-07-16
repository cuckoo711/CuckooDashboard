"""设置后台与 DPAPI Vault 集成测试。"""

from __future__ import annotations

import copy
import json

import pytest

import services.settings_service as settings_service
from core.credentials import VaultConflict


class MemoryVault:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state or {"version": 1, "revision": 0, "global": {}, "providers": {}})

    def get_revision(self):
        return self.state["revision"]

    def update(self, mutator, *, expected_revision=None):
        if expected_revision is not None and expected_revision != self.state["revision"]:
            raise VaultConflict("stale")
        candidate = copy.deepcopy(self.state)
        result = mutator(candidate)
        if result is not None:
            candidate = result
        candidate["revision"] = self.state["revision"] + 1
        self.state = candidate
        return copy.deepcopy(candidate)


@pytest.fixture
def base_config():
    return {
        "config_version": 3,
        "dashboard": {
            "off_peak_badge": {"enabled": True, "ranges": [{"start": "00:00", "end": "08:00"}]},
            "vibe_coding": {"ring": {}, "model_bars": {}, "balances": []},
        },
        "providers": {
            "mimo": {"enabled": True},
            "local_platform": {"enabled": True, "urls": [{"url": "http://one.example", "credential_ref": "local-one"}]},
            "nug": {"enabled": False},
        },
        "hardware_overrides": {
            "cpu_model": None, "mem_installed_gb": None, "mem_name": None,
            "gpu_model": None, "gpu_vram_gb": {}, "apu_device_ids": None,
        },
        "logging": {
            "level": "INFO", "mode": "daily", "dir": "logs",
            "keep_days": 7, "max_size_mb": 5, "max_backups": 5, "console": True,
        },
        "theme": "dark",
        "lyric_offset": 0.0,
        "vibe_active": False,
        "custom_unknown": {"keep": True},
    }


@pytest.fixture
def memory_vault(monkeypatch):
    store = MemoryVault({
        "version": 1,
        "revision": 3,
        "global": {"dashboard_token": "dashboard-secret", "github_token": "github-secret"},
        "providers": {},
    })
    monkeypatch.setattr(settings_service, "vault", store)
    monkeypatch.setattr(settings_service, "get_global_secret", lambda key, default=None: copy.deepcopy(store.state["global"].get(key, default)))
    return store


def test_public_global_payload_masks_vault_secrets(base_config, memory_vault):
    payload = settings_service._public_global_config(base_config)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["dashboard"]["token"]["configured"] is True
    assert payload["github_token"]["configured"] is True
    assert "dashboard-secret" not in serialized
    assert "github-secret" not in serialized


def test_save_moves_global_secrets_to_vault_and_keeps_yaml_clean(monkeypatch, base_config, memory_vault):
    saved: dict = {}
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: (["test"], []))
    monkeypatch.setattr(settings_service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})

    result = settings_service.save_settings_payload({
        "config": {"dashboard": {}, "providers": {}},
        "secrets": {
            "dashboard.token": {"action": "set", "value": "new-dashboard"},
            "github_token": {"action": "clear"},
        },
        "credential_revision": 3,
    })

    assert result["ok"] is True
    assert "token" not in saved["dashboard"]
    assert "github_token" not in saved
    assert memory_vault.state["global"]["dashboard_token"] == "new-dashboard"
    assert "github_token" not in memory_vault.state["global"]
    assert saved["custom_unknown"] == {"keep": True}


def test_schema_secret_is_stored_in_provider_vault_state(monkeypatch, base_config, memory_vault):
    fake_schema = [{
        "provider": "atlas", "config_key": "atlas", "title": "Atlas", "description": "", "order": 1,
        "fields": [
            {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
            {"key": "api_key", "label": "API Key", "type": "secret", "default": ""},
        ],
    }]
    fake_provider = type("FakeProvider", (), {"get_status": lambda self: {"status": "unknown", "ok": False, "enabled": True}})()
    base = copy.deepcopy(base_config)
    base["providers"] = {"atlas": {"enabled": True}}
    memory_vault.state["providers"]["atlas"] = {"config_secrets": {"fields": {"api_key": "old-key"}, "objects": {}}}
    saved: dict = {}

    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(settings_service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})
    monkeypatch.setattr(settings_service, "get_provider_config_schemas", lambda: copy.deepcopy(fake_schema))
    monkeypatch.setattr(settings_service, "get_providers", lambda: {"atlas": fake_provider})
    monkeypatch.setattr(settings_service, "get_provider_config", lambda name, default=None: {"enabled": True, "api_key": memory_vault.state["providers"]["atlas"]["config_secrets"]["fields"]["api_key"]})

    settings_service.save_settings_payload({
        "config": {"providers": {"atlas": {"enabled": True}}},
        "secrets": {"providers.atlas.api_key": {"action": "set", "value": "new-key"}},
        "credential_revision": 3,
    })

    assert "api_key" not in saved["providers"]["atlas"]
    assert memory_vault.state["providers"]["atlas"]["config_secrets"]["fields"]["api_key"] == "new-key"
    assert settings_service.reveal_secret("providers.atlas.api_key") == "new-key"


def test_stale_credential_revision_is_rejected(monkeypatch, base_config, memory_vault):
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))

    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.save_settings_payload({
            "config": {"providers": {}},
            "secrets": {"github_token": {"action": "set", "value": "x"}},
            "credential_revision": 1,
        })
