"""Tests for canonical providers configuration migration."""

from __future__ import annotations

from core.config import CONFIG_VERSION, migrate_config


def test_legacy_provider_sections_are_migrated_and_normalized():
    legacy = {
        "dashboard": {},
        "local_platforms": {
            "enabled": True,
            "username": "user",
            "password": "secret",
            "urls": ["http://one.example", {"url": "https://two.example", "password": "two"}],
        },
        "nug": {"enabled": True, "url": "https://nug.example"},
        "custom_unknown": {"keep": True},
    }

    migrated, changed = migrate_config(legacy)

    assert changed is True
    assert migrated["config_version"] == CONFIG_VERSION
    assert "local_platforms" not in migrated
    assert "nug" not in migrated
    assert migrated["providers"]["local_platform"]["urls"] == [
        {"url": "http://one.example"},
        {"url": "https://two.example", "password": "two"},
    ]
    assert migrated["providers"]["nug"]["url"] == "https://nug.example"
    assert migrated["providers"]["mimo"]["enabled"] is True
    assert migrated["custom_unknown"] == {"keep": True}


def test_canonical_provider_values_win_when_legacy_and_new_both_exist():
    config = {
        "config_version": 2,
        "providers": {
            "local_platform": {"username": "canonical", "enabled": False},
        },
        "local_platforms": {"username": "legacy", "password": "legacy-secret"},
    }

    migrated, changed = migrate_config(config)

    assert changed is True
    assert migrated["providers"]["local_platform"]["username"] == "canonical"
    assert migrated["providers"]["local_platform"]["enabled"] is False
    assert migrated["providers"]["local_platform"]["password"] == "legacy-secret"


def test_migration_is_idempotent():
    first, first_changed = migrate_config({"local_platforms": {"enabled": False}})
    second, second_changed = migrate_config(first)

    assert first_changed is True
    assert second_changed is False
    assert second == first
