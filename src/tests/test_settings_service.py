"""Tests for the Schema-driven settings backend."""

from __future__ import annotations

import copy
import json

import pytest

import services.settings_service as settings_service


@pytest.fixture
def base_config():
    return {
        "config_version": 2,
        "dashboard": {
            "token": "dashboard-secret",
            "off_peak_badge": {"enabled": True, "ranges": [{"start": "00:00", "end": "08:00"}]},
            "vibe_coding": {"ring": {}, "model_bars": {}, "balances": []},
        },
        "github_token": "github-secret",
        "providers": {
            "mimo": {"enabled": True},
            "local_platform": {
                "enabled": True,
                "username": "user",
                "password": "default-secret",
                "urls": [{"url": "http://one.example", "password": "instance-secret"}],
            },
            "nug": {"enabled": False, "url": "", "username": "", "password": "nug-secret"},
        },
        "hardware_overrides": {
            "cpu_model": None,
            "mem_installed_gb": None,
            "mem_name": None,
            "gpu_model": None,
            "gpu_vram_gb": {},
            "apu_device_ids": None,
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


def test_public_payload_masks_provider_secrets(monkeypatch, base_config):
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "get_settings_options", lambda: {})
    payload = settings_service.get_settings_payload()
    serialized = json.dumps(payload, ensure_ascii=False)
    panels = {panel["config_key"]: panel for panel in payload["providers"]}

    assert panels["local_platform"]["values"]["password"]["configured"] is True
    assert panels["local_platform"]["values"]["urls"][0]["password"]["configured"] is True
    assert panels["nug"]["values"]["password"]["configured"] is True
    assert payload["config"]["github_token"]["configured"] is True
    for secret in ("dashboard-secret", "github-secret", "default-secret", "instance-secret", "nug-secret"):
        assert secret not in serialized


def _save_payload():
    return {
        "config": {
            "dashboard": {
                "off_peak_badge": {"enabled": False, "ranges": []},
                "vibe_coding": {"ring": {}, "model_bars": {}, "balances": []},
            },
            "providers": {
                "mimo": {"enabled": True},
                "local_platform": {
                    "enabled": True,
                    "username": "changed",
                    "urls": [
                        {"url": "http://one.example", "__original_url": "http://one.example"},
                        {"url": "https://two.example", "__original_url": ""},
                    ],
                },
                "nug": {"enabled": True, "url": "https://nug.example", "username": "n"},
            },
            "hardware_overrides": {
                "cpu_model": "CPU override", "mem_installed_gb": 32, "mem_name": None,
                "gpu_model": None, "gpu_vram_gb": {"GPU": 16}, "apu_device_ids": None,
            },
            "logging": {
                "level": "DEBUG", "mode": "daily", "dir": "logs",
                "keep_days": 7, "max_size_mb": 5, "max_backups": 5, "console": True,
            },
            "theme": "dark", "lyric_offset": 0, "vibe_active": False,
        },
        "secrets": {
            "dashboard.token": {"action": "keep"},
            "github_token": {"action": "keep"},
            "providers.local_platform.password": {"action": "keep"},
            "providers.local_platform.urls": [
                {
                    "original_identity": "http://one.example",
                    "identity": "http://one.example",
                    "fields": {"password": {"action": "keep"}},
                },
                {
                    "original_identity": "https://two.example",
                    "identity": "https://two.example",
                    "fields": {"password": {"action": "set", "value": "two-secret"}},
                },
            ],
            "providers.nug.password": {"action": "set", "value": "new-nug"},
        },
    }


def test_save_preserves_provider_secrets_and_unknown_keys(monkeypatch, base_config):
    saved: dict = {}
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: (["test"], []))
    monkeypatch.setattr(settings_service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})

    result = settings_service.save_settings_payload(_save_payload())

    assert result["ok"] is True
    assert saved["dashboard"]["token"] == "dashboard-secret"
    assert saved["github_token"] == "github-secret"
    assert saved["providers"]["local_platform"]["password"] == "default-secret"
    assert saved["providers"]["local_platform"]["urls"][0]["password"] == "instance-secret"
    assert saved["providers"]["local_platform"]["urls"][1]["password"] == "two-secret"
    assert saved["providers"]["nug"]["password"] == "new-nug"
    assert saved["custom_unknown"] == {"keep": True}


def test_save_can_clear_provider_secrets(monkeypatch, base_config):
    saved: dict = {}
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(settings_service, "get_settings_payload", lambda: {"config": {}, "providers": [], "options": {}})
    payload = _save_payload()
    payload["secrets"]["providers.local_platform.password"] = {"action": "clear"}
    payload["secrets"]["providers.nug.password"] = {"action": "clear"}
    payload["secrets"]["providers.local_platform.urls"][0]["fields"]["password"] = {"action": "clear"}

    settings_service.save_settings_payload(payload)

    assert saved["providers"]["local_platform"]["password"] == ""
    assert saved["providers"]["local_platform"]["urls"][0].get("password", "") == ""
    assert saved["providers"]["nug"]["password"] == ""


def test_invalid_global_and_provider_values_are_rejected():
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service._validate_off_peak({"enabled": True, "ranges": [{"start": "09:00", "end": "09:00"}]})
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service._http_url("not-a-url", "providers.nug.url")


def test_reveal_uses_provider_schema_allowlist(monkeypatch, base_config):
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    assert settings_service.reveal_secret("dashboard.token") == "dashboard-secret"
    assert settings_service.reveal_secret(
        "providers.local_platform.urls", identity="http://one.example", field="password"
    ) == "instance-secret"
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.reveal_secret("providers.local_platform.unknown")
