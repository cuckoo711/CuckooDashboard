"""DPAPI Vault 存储层的可移植单元测试（使用测试 cipher）。"""

from __future__ import annotations

import base64

import pytest

from core.credentials import CredentialVault, VaultConflict, VaultDecryptError


class TestCipher:
    """仅用于测试格式与原子更新，不模拟真实 DPAPI 安全性。"""

    def encrypt(self, plaintext: bytes) -> bytes:
        return base64.b85encode(plaintext[::-1])

    def decrypt(self, ciphertext: bytes) -> bytes:
        return base64.b85decode(ciphertext)[::-1]


def make_vault(tmp_path):
    return CredentialVault(
        tmp_path / "credentials.vault",
        lock_path=tmp_path / "credentials.vault.lock",
        cipher=TestCipher(),
    )


def test_vault_encrypts_payload_and_preserves_namespaces(tmp_path):
    store = make_vault(tmp_path)
    secret = "test-secret-value"

    store.set_global_secret("github_token", secret)
    store.update_provider_state("atlas", lambda state: {"accounts": {"one": {"token": "atlas-token"}}})

    disk = (tmp_path / "credentials.vault").read_text(encoding="utf-8")
    assert secret not in disk
    assert "atlas-token" not in disk
    assert store.get_global_secret("github_token") == secret
    assert store.get_provider_state("atlas")["accounts"]["one"]["token"] == "atlas-token"
    assert store.get_revision() == 2


def test_vault_updates_are_revision_guarded_and_do_not_overwrite_other_providers(tmp_path):
    store = make_vault(tmp_path)
    first = store.replace_provider_state("one", {"value": 1})
    store.replace_provider_state("two", {"value": 2})

    with pytest.raises(VaultConflict):
        store.replace_provider_state("one", {"value": 3}, expected_revision=first["revision"])

    store.update_provider_state("one", lambda state: {**state, "value": 3})
    assert store.get_provider_state("one")["value"] == 3
    assert store.get_provider_state("two")["value"] == 2


def test_vault_rejects_corrupt_payload(tmp_path):
    store = make_vault(tmp_path)
    store.path.write_text('{"format_version":1,"cipher":"dpapi-current-user","payload":"bad"}', encoding="utf-8")

    with pytest.raises(VaultDecryptError):
        store.read()
