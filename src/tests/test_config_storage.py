"""Tests for strict schema-v4 configuration storage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core import config as config_module
from core.config import CONFIG_VERSION, ConfigError, ConfigVersionError


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.yaml")
    monkeypatch.setattr(config_module, "CONFIG_LOCK_FILE", config_dir / ".config-write.lock")
    config_module.invalidate_config_cache()
    yield config_dir / "config.yaml"
    config_module.invalidate_config_cache()


def test_missing_config_uses_new_install_v4_defaults(isolated_config):
    assert config_module.load_config() == {"config_version": CONFIG_VERSION, "providers": {}}
    assert not isolated_config.exists()


def test_load_config_preserves_existing_v4_structure_without_conversion(isolated_config):
    isolated_config.parent.mkdir()
    isolated_config.write_text(
        "config_version: 4\nproviders:\n  future-provider:\n    arbitrary:\n      nested: true\ncustom_unknown:\n  keep: true\n",
        encoding="utf-8",
    )

    loaded = config_module.load_config()

    assert loaded["providers"]["future-provider"] == {"arbitrary": {"nested": True}}
    assert loaded["custom_unknown"] == {"keep": True}
    assert "legacy" not in loaded


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("config_version: 3\nproviders: {}\n", ConfigVersionError),
        ("providers: {}\n", ConfigVersionError),
        ("config_version: 4\nproviders: []\n", ConfigError),
    ],
)
def test_existing_non_v4_or_malformed_config_is_rejected(isolated_config, content, error):
    isolated_config.parent.mkdir()
    isolated_config.write_text(content, encoding="utf-8")

    with pytest.raises(error):
        config_module.load_config()


def test_save_config_rejects_legacy_version(isolated_config):
    with pytest.raises(ConfigVersionError):
        config_module.save_config({"config_version": 3, "providers": {}})


def test_save_config_serializes_concurrent_v4_writes(isolated_config):
    def save(value: str) -> None:
        config_module.save_config({
            "config_version": CONFIG_VERSION,
            "providers": {"example": {"value": value}},
        })

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(save, ("first", "second")))

    config_module.invalidate_config_cache()
    persisted = config_module.load_config()
    assert persisted["config_version"] == CONFIG_VERSION
    assert persisted["providers"]["example"]["value"] in {"first", "second"}
