"""v2 明文认证数据向 DPAPI Vault 的迁移测试。"""

from __future__ import annotations

import base64
import copy
import json

import core.credential_migration as migration
from core.credentials import CredentialVault


class TestCipher:
    def encrypt(self, plaintext: bytes) -> bytes:
        return base64.b85encode(plaintext[::-1])

    def decrypt(self, ciphertext: bytes) -> bytes:
        return base64.b85decode(ciphertext)[::-1]


def test_migration_moves_all_known_credentials_and_sanitizes_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    cookies = config_dir / "cookies.json"
    tokens = data_dir / "local_tokens.json"
    cookies.write_text(json.dumps({"cookie": "mimo-cookie", "userId": "42", "passToken": "refresh"}), encoding="utf-8")
    tokens.write_text(json.dumps({"https://one.example": {"token": "jwt", "ts": 100}}), encoding="utf-8")

    store = CredentialVault(tmp_path / "credentials.vault", lock_path=tmp_path / "credentials.lock", cipher=TestCipher())
    monkeypatch.setattr(migration, "vault", store)
    monkeypatch.setattr(migration, "LEGACY_MIMO_COOKIES", cookies)
    monkeypatch.setattr(migration, "LEGACY_LOCAL_TOKENS", tokens)

    legacy = {
        "config_version": 2,
        "dashboard": {"token": "dashboard-token"},
        "github_token": "github-token",
        "providers": {
            "mimo": {"enabled": True},
            "nug": {"enabled": True, "url": "https://nug.example", "username": "nug-user", "password": "nug-pass"},
            "local_platform": {
                "enabled": True,
                "username": "local-user",
                "password": "local-pass",
                "urls": [
                    {"url": "https://one.example", "password": "override-pass"},
                    {"url": "https://two.example"},
                ],
            },
        },
    }

    cleaned, changed, cleanup = migration.migrate_legacy_credentials(copy.deepcopy(legacy))
    vault_state = store.read()

    assert changed is True
    assert "github_token" not in cleaned
    assert "token" not in cleaned["dashboard"]
    assert set(cleanup) == {cookies, tokens}
    assert vault_state["global"] == {"dashboard_token": "dashboard-token", "github_token": "github-token"}
    assert vault_state["providers"]["mimo"]["accounts"]["mimo-42"]["passToken"] == "refresh"
    assert len(vault_state["providers"]["nug"]["accounts"]) == 1
    assert len(vault_state["providers"]["local_platform"]["accounts"]) == 2
    urls = cleaned["providers"]["local_platform"]["urls"]
    assert all("password" not in row for row in urls)
    assert all(row["credential_ref"] for row in urls)


def test_migration_never_marks_invalid_legacy_json_for_deletion(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    cookies = config_dir / "cookies.json"
    cookies.write_text("not-json", encoding="utf-8")
    tokens = data_dir / "local_tokens.json"
    tokens.write_text("not-json", encoding="utf-8")

    store = CredentialVault(tmp_path / "credentials.vault", lock_path=tmp_path / "credentials.lock", cipher=TestCipher())
    monkeypatch.setattr(migration, "vault", store)
    monkeypatch.setattr(migration, "LEGACY_MIMO_COOKIES", cookies)
    monkeypatch.setattr(migration, "LEGACY_LOCAL_TOKENS", tokens)

    cleaned, changed, cleanup = migration.migrate_legacy_credentials({"providers": {}, "dashboard": {}})

    assert cleaned["providers"] == {}
    assert changed is False
    assert cleanup == []
    assert cookies.exists() and tokens.exists()
