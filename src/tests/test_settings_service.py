"""Tests for the local settings backend service."""

from __future__ import annotations

import copy
import json

import pytest

import services.settings_service as settings_service


@pytest.fixture
def base_config():
    return {
        "dashboard": {
            "token": "dashboard-secret",
            "off_peak_badge": {
                "enabled": True,
                "ranges": [{"start": "00:00", "end": "08:00"}],
            },
            "vibe_coding": {
                "ring": {},
                "model_bars": {},
                "balances": [],
            },
        },
        "github_token": "github-secret",
        "local_platforms": {
            "enabled": True,
            "username": "user",
            "password": "default-secret",
            "urls": [{"url": "http://one.example", "password": "instance-secret"}],
        },
        "nug": {
            "enabled": False,
            "url": "",
            "username": "",
            "password": "nug-secret",
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
            "level": "INFO",
            "mode": "daily",
            "dir": "logs",
            "keep_days": 7,
            "max_size_mb": 5,
            "max_backups": 5,
            "console": True,
        },
        "theme": "dark",
        "lyric_offset": 0.0,
        "vibe_active": False,
        "custom_unknown": {"keep": True},
    }


def test_public_config_masks_all_sensitive_values(base_config):
    public = settings_service._public_config(base_config)
    serialized = json.dumps(public, ensure_ascii=False)

    assert public["dashboard"]["token"]["configured"] is True
    assert public["github_token"]["configured"] is True
    assert public["local_platforms"]["password"]["configured"] is True
    assert public["local_platforms"]["urls"][0]["password"]["configured"] is True
    for secret in ("dashboard-secret", "github-secret", "default-secret", "instance-secret", "nug-secret"):
        assert secret not in serialized


def test_save_keeps_existing_secrets_and_unknown_keys(monkeypatch, base_config):
    saved: dict = {}
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: (["test"], []))
    monkeypatch.setattr(settings_service, "get_settings_options", lambda: {})

    result = settings_service.save_settings_payload({
        "config": {
            "dashboard": {
                "off_peak_badge": {"enabled": False, "ranges": []},
                "vibe_coding": {"ring": {}, "model_bars": {}, "balances": []},
            },
            "local_platforms": {
                "enabled": True,
                "username": "changed",
                "urls": [{"url": "http://one.example", "original_url": "http://one.example"}],
            },
            "nug": {"enabled": False, "url": "", "username": ""},
            "hardware_overrides": {
                "cpu_model": "CPU override",
                "mem_installed_gb": 32,
                "mem_name": None,
                "gpu_model": None,
                "gpu_vram_gb": {"GPU": 16},
                "apu_device_ids": None,
            },
            "logging": {
                "level": "DEBUG",
                "mode": "daily",
                "dir": "logs",
                "keep_days": 7,
                "max_size_mb": 5,
                "max_backups": 5,
                "console": True,
            },
            "theme": "dark",
            "lyric_offset": 0,
            "vibe_active": False,
        },
        "secrets": {
            "dashboard.token": {"action": "keep"},
            "github_token": {"action": "keep"},
            "local_platforms.password": {"action": "keep"},
            "nug.password": {"action": "keep"},
            "local_platforms.url_passwords": [
                {"url": "http://one.example", "original_url": "http://one.example", "action": "keep"},
            ],
        },
    })

    assert result["ok"] is True
    assert saved["dashboard"]["token"] == "dashboard-secret"
    assert saved["github_token"] == "github-secret"
    assert saved["local_platforms"]["password"] == "default-secret"
    assert saved["local_platforms"]["urls"][0]["password"] == "instance-secret"
    assert saved["nug"]["password"] == "nug-secret"
    assert saved["custom_unknown"] == {"keep": True}


def test_save_can_set_and_clear_secrets(monkeypatch, base_config):
    saved: dict = {}
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    monkeypatch.setattr(settings_service, "save_config", lambda value: saved.update(copy.deepcopy(value)))
    monkeypatch.setattr(settings_service, "apply_runtime_config", lambda: ([], []))
    monkeypatch.setattr(settings_service, "get_settings_options", lambda: {})

    settings_service.save_settings_payload({
        "config": {
            "dashboard": {"off_peak_badge": base_config["dashboard"]["off_peak_badge"], "vibe_coding": base_config["dashboard"]["vibe_coding"]},
            "local_platforms": {"enabled": True, "username": "user", "urls": []},
            "nug": {"enabled": False, "url": "", "username": ""},
            "hardware_overrides": base_config["hardware_overrides"],
            "logging": base_config["logging"],
            "theme": "dark",
            "lyric_offset": 0,
            "vibe_active": False,
        },
        "secrets": {
            "dashboard.token": {"action": "set", "value": "new-dashboard"},
            "github_token": {"action": "clear"},
            "local_platforms.password": {"action": "clear"},
            "nug.password": {"action": "set", "value": "new-nug"},
            "local_platforms.url_passwords": [],
        },
    })

    assert saved["dashboard"]["token"] == "new-dashboard"
    assert saved["github_token"] == ""
    assert saved["local_platforms"]["password"] == ""
    assert saved["nug"]["password"] == "new-nug"


def test_invalid_range_and_url_are_rejected():
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service._validate_off_peak({"enabled": True, "ranges": [{"start": "09:00", "end": "09:00"}]})
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service._http_url("not-a-url", "nug.url")


def test_reveal_uses_allowlisted_paths(monkeypatch, base_config):
    monkeypatch.setattr(settings_service, "load_config", lambda: copy.deepcopy(base_config))
    assert settings_service.reveal_secret("dashboard.token") == "dashboard-secret"
    assert settings_service.reveal_secret("local_platforms.urls[0].password") == "instance-secret"
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.reveal_secret("custom_unknown")
