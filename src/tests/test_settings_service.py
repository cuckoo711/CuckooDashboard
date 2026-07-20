"""设置后台与 DPAPI Vault 集成测试。"""

from __future__ import annotations

import copy
import json

import pytest

from core.credentials import VaultConflict
from features.settings import persistence, runtime, schema, service


class MemoryVault:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state or {"version": 1, "revision": 0, "global": {}, "providers": {}})

    def get_revision(self):
        return self.state["revision"]

    def get_provider_state(self, provider_id, default=None):
        return copy.deepcopy(self.state["providers"].get(provider_id, default))

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
        "config_version": 4,
        "dashboard": {
            "off_peak_badge": {"enabled": True, "ranges": [{"start": "00:00", "end": "08:00"}]},
            "vibe_coding": {"ring": {}, "model_bars": {}, "balances": []},
        },
        "providers": {
            "mimo": {"enabled": True},
            "nfk": {"enabled": True, "urls": [{"url": "http://one.example", "credential_ref": "local-one"}]},
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
    monkeypatch.setattr(persistence, "vault", store)
    monkeypatch.setattr(
        persistence,
        "get_global_secret",
        lambda key, default=None: copy.deepcopy(store.state["global"].get(key, default)),
    )
    return store


def test_public_global_payload_masks_vault_secrets(base_config, memory_vault):
    payload = service._public_global_config(base_config)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["dashboard"]["token"]["configured"] is True
    assert payload["github_token"]["configured"] is True
    assert "dashboard-secret" not in serialized
    assert "github-secret" not in serialized


def test_keep_secret_actions_do_not_write_vault_or_require_revision(monkeypatch, base_config, memory_vault):
    """Ordinary Settings saves should not collide with auth refresh revisions."""
    saved: dict = {}
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: (["test"], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {
        "config": {},
        "providers": [],
        "options": {},
        "credential_revision": 99,
    })
    memory_vault.state["revision"] = 9  # newer than the stale client revision
    result = service.save_settings_payload({
        "config": {
            "theme": "dark",
            "music": {
                "spectrum_enabled": True,
                "capture_device": "auto",
                "spectrum_offset_ms": 40,
                "bins": 48,
                "render_fps": 0,
                "render_bars": 0,
            },
            "providers": {},
        },
        "secrets": {
            "dashboard.token": {"action": "keep"},
            "github_token": {"action": "keep"},
        },
        # Intentionally stale: must be ignored when no secret mutation happens.
        "credential_revision": 1,
    })
    assert result["ok"] is True
    assert memory_vault.state["revision"] == 9
    assert saved["theme"] == "dark"


def test_save_moves_global_secrets_to_vault_and_keeps_yaml_clean(monkeypatch, base_config, memory_vault):
    saved: dict = {}
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: (["test"], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})

    result = service.save_settings_payload({
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

    provider_config = lambda name, default=None: {
        "enabled": True,
        "api_key": memory_vault.state["providers"]["atlas"]["config_secrets"]["fields"]["api_key"],
    }
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})
    monkeypatch.setattr(schema, "get_provider_config_schemas", lambda: copy.deepcopy(fake_schema))
    monkeypatch.setattr(schema, "get_providers", lambda: {"atlas": fake_provider})
    monkeypatch.setattr(schema, "get_provider_config", provider_config)
    monkeypatch.setattr(persistence, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(persistence, "get_provider_config", provider_config)

    service.save_settings_payload({
        "config": {"providers": {"atlas": {"enabled": True}}},
        "secrets": {"providers.atlas.api_key": {"action": "set", "value": "new-key"}},
        "credential_revision": 3,
    })

    assert "api_key" not in saved["providers"]["atlas"]
    assert memory_vault.state["providers"]["atlas"]["config_secrets"]["fields"]["api_key"] == "new-key"
    assert persistence.reveal_secret("providers.atlas.api_key") == "new-key"


def test_object_list_rename_rekeys_vault_secret(monkeypatch, base_config, memory_vault):
    """行改名（keep + 新 identity）必须把 Vault secret 重新键控到新 identity。"""
    fake_schema = [{
        "provider": "atlas", "config_key": "atlas", "title": "Atlas", "description": "", "order": 1,
        "fields": [
            {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
            {
                "key": "urls", "label": "实例", "type": "object_list", "identity_key": "url",
                "item_fields": [
                    {"key": "url", "label": "URL", "type": "url"},
                    {"key": "password", "label": "密码", "type": "secret", "default": ""},
                ],
                "default": [],
            },
        ],
    }]
    fake_provider = type("FakeProvider", (), {"get_status": lambda self: {"status": "unknown", "ok": False, "enabled": True}})()
    base = copy.deepcopy(base_config)
    base["providers"] = {"atlas": {"enabled": True, "urls": [{"url": "http://old.example"}]}}
    memory_vault.state["providers"]["atlas"] = {
        "config_secrets": {"fields": {}, "objects": {"urls": {"http://old.example": {"password": "s3cret"}}}},
    }
    saved: dict = {}

    provider_config = lambda name, default=None: {
        "enabled": True,
        "urls": [{"url": "http://old.example", "password": "s3cret"}],
    }
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})
    monkeypatch.setattr(schema, "get_provider_config_schemas", lambda: copy.deepcopy(fake_schema))
    monkeypatch.setattr(schema, "get_providers", lambda: {"atlas": fake_provider})
    monkeypatch.setattr(schema, "get_provider_config", provider_config)
    monkeypatch.setattr(persistence, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(persistence, "get_provider_config", provider_config)

    service.save_settings_payload({
        "config": {"providers": {"atlas": {
            "enabled": True,
            "urls": [{"url": "http://new.example", "__original_url": "http://old.example"}],
        }}},
        "secrets": {},
        "credential_revision": 3,
    })

    objects = memory_vault.state["providers"]["atlas"]["config_secrets"]["objects"]
    assert objects == {"urls": {"http://new.example": {"password": "s3cret"}}}
    assert saved["providers"]["atlas"]["urls"] == [{"url": "http://new.example"}]


def test_dashboard_token_saved_without_config_dashboard(monkeypatch, base_config, memory_vault):
    """只带 secrets 不带 config.dashboard 的部分保存也必须写入 token。"""
    saved: dict = {}
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})

    result = service.save_settings_payload({
        "config": {"providers": {}},
        "secrets": {"dashboard.token": {"action": "set", "value": "partial-save-token"}},
        "credential_revision": 3,
    })

    assert result["ok"] is True
    assert memory_vault.state["global"]["dashboard_token"] == "partial-save-token"


def test_music_save_preserves_unmanaged_keys(monkeypatch, base_config, memory_vault):
    """设置页保存 music 时不能抹掉 orbit 等设置页不管理的运行时键。"""
    base = copy.deepcopy(base_config)
    base["music"] = {"spectrum_enabled": True, "orbit_yaw": 12.5, "orbit_pitch": -3.0}
    saved: dict = {}
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base))
    monkeypatch.setattr(service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(runtime, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})
    monkeypatch.setattr(service, "list_capture_devices", lambda: [])
    monkeypatch.setattr(service, "load_music_offsets", lambda: {"capture_device": "auto", "spectrum_enabled": True})
    monkeypatch.setattr(service, "request_capture_restart", lambda reason: None)

    service.save_settings_payload({
        "config": {"providers": {}, "music": {"spectrum_enabled": False, "capture_device": "auto"}},
        "secrets": {},
    })

    assert saved["music"]["spectrum_enabled"] is False
    assert saved["music"]["orbit_yaw"] == 12.5
    assert saved["music"]["orbit_pitch"] == -3.0


def test_stale_credential_revision_is_rejected(monkeypatch, base_config, memory_vault):
    monkeypatch.setattr(service, "load_config", lambda: copy.deepcopy(base_config))

    with pytest.raises(schema.SettingsValidationError):
        service.save_settings_payload({
            "config": {"providers": {}},
            "secrets": {"github_token": {"action": "set", "value": "x"}},
            "credential_revision": 1,
        })
