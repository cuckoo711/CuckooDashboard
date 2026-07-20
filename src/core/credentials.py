"""Windows DPAPI 加密凭据 Vault。

Vault 是所有 Provider 账户、会话、JWT 及全局 token 的唯一持久化来源。
文件内容整体使用当前 Windows 用户的 DPAPI 加密；调用方只能通过本模块的
namespace API 读写，避免在配置、缓存或日志中散落明文凭据。
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger("cuckoo.credentials")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
VAULT_FILE = CONFIG_DIR / "credentials.vault"
LOCK_FILE = CONFIG_DIR / "credentials.vault.lock"
VAULT_FORMAT_VERSION = 1
_PAYLOAD_DESCRIPTION = "Cuckoo Dashboard Credentials"

T = TypeVar("T")


class VaultError(RuntimeError):
    """凭据 Vault 的基础异常。"""


class VaultUnavailable(VaultError):
    """当前环境无法使用 Windows DPAPI。"""


class VaultDecryptError(VaultError):
    """Vault 存在但当前用户/机器无法解密或内容损坏。"""


class VaultConflict(VaultError):
    """客户端基于过期 revision 提交了更新。"""


class DPAPICipher:
    """当前 Windows 用户作用域的 DPAPI 加解密实现。"""

    @staticmethod
    def _module():
        try:
            import win32crypt  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Windows 项目外的显式报错路径
            raise VaultUnavailable("当前环境缺少 pywin32/win32crypt，无法使用 DPAPI Vault") from exc
        return win32crypt

    def encrypt(self, plaintext: bytes) -> bytes:
        win32crypt = self._module()
        try:
            flags = getattr(win32crypt, "CRYPTPROTECT_UI_FORBIDDEN", 0)
            result = win32crypt.CryptProtectData(
                plaintext,
                _PAYLOAD_DESCRIPTION,
                None,
                None,
                None,
                flags,
            )
            # pywin32 版本之间返回值不一致：常见版本直接返回 bytes，少数版本返回二元组。
            encrypted = result[1] if isinstance(result, tuple) else result
            return bytes(encrypted)
        except Exception as exc:  # pragma: no cover - 依赖当前 Windows 用户 DPAPI 环境
            raise VaultUnavailable("无法使用 Windows DPAPI 加密凭据") from exc

    def decrypt(self, ciphertext: bytes) -> bytes:
        win32crypt = self._module()
        try:
            flags = getattr(win32crypt, "CRYPTPROTECT_UI_FORBIDDEN", 0)
            result = win32crypt.CryptUnprotectData(
                ciphertext,
                None,
                None,
                None,
                flags,
            )
            plaintext = result[1] if isinstance(result, tuple) else result
            return bytes(plaintext)
        except Exception as exc:  # pragma: no cover - 依赖当前 Windows 用户 DPAPI 环境
            raise VaultDecryptError("凭据 Vault 无法解密；请使用当前 Windows 用户重新认证") from exc


class CredentialVault:
    """线程和进程安全的加密凭据 Vault。

    Provider state 是不透明的 dict，由 Provider 自己定义账户、活动账户与会话结构。
    每一次更新都从磁盘读取最新数据，在同一锁内完成修改和原子替换，避免多个
    进程（Dashboard、CLI、后台刷新）相互覆盖。
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        lock_path: Path | None = None,
        cipher: Any | None = None,
        lock_timeout: float = 10.0,
    ):
        self.path = Path(path or VAULT_FILE)
        self.lock_path = Path(lock_path or LOCK_FILE)
        self.cipher = cipher or DPAPICipher()
        self.lock_timeout = lock_timeout
        self._thread_lock = threading.RLock()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": VAULT_FORMAT_VERSION,
            "revision": 0,
            "global": {},
            "providers": {},
        }

    @staticmethod
    def _normalise_state(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise VaultDecryptError("凭据 Vault 的解密内容格式无效")
        version = value.get("version", VAULT_FORMAT_VERSION)
        if version != VAULT_FORMAT_VERSION:
            raise VaultDecryptError(f"不支持的凭据 Vault 版本: {version}")
        revision = value.get("revision", 0)
        if not isinstance(revision, int) or revision < 0:
            raise VaultDecryptError("凭据 Vault revision 无效")
        global_values = value.get("global", {})
        providers = value.get("providers", {})
        if not isinstance(global_values, dict) or not isinstance(providers, dict):
            raise VaultDecryptError("凭据 Vault 的 namespace 格式无效")
        return {
            "version": VAULT_FORMAT_VERSION,
            "revision": revision,
            "global": copy.deepcopy(global_values),
            "providers": copy.deepcopy(providers),
        }

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        """使用标准库提供跨进程 advisory lock，Windows 为主并兼容 POSIX 测试。"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout
        with open(self.lock_path, "a+b") as handle:
            # 锁定字节 0 前确保文件非空；a+ 模式写入总是追加，
            # 所以必须按实际大小判断，否则每次加锁都会让锁文件增长一字节。
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            acquired = False
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - 仅用于非 Windows 单元测试环境
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise VaultError("等待凭据 Vault 锁超时")
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover - 仅用于非 Windows 单元测试环境
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            with self._process_lock():
                yield

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_state()
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(wrapper, dict):
                raise VaultDecryptError("凭据 Vault 外层格式无效")
            if wrapper.get("format_version") != VAULT_FORMAT_VERSION:
                raise VaultDecryptError("不支持的凭据 Vault 文件版本")
            if wrapper.get("cipher") != "dpapi-current-user":
                raise VaultDecryptError("不支持的凭据 Vault 加密方式")
            encoded = wrapper.get("payload")
            if not isinstance(encoded, str) or not encoded:
                raise VaultDecryptError("凭据 Vault 缺少密文")
            decrypted = self.cipher.decrypt(base64.b64decode(encoded.encode("ascii"), validate=True))
            return self._normalise_state(json.loads(decrypted.decode("utf-8")))
        except VaultError:
            raise
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise VaultDecryptError("凭据 Vault 损坏或无法读取") from exc

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        state = self._normalise_state(state)
        plain = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encrypted = self.cipher.encrypt(plain)
        wrapper = {
            "format_version": VAULT_FORMAT_VERSION,
            "cipher": "dpapi-current-user",
            "payload": base64.b64encode(encrypted).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")))
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def read(self) -> dict[str, Any]:
        """返回完整 Vault 快照的深拷贝；缺失 Vault 视为初始空状态。"""
        with self._locked():
            return copy.deepcopy(self._read_unlocked())

    def get_revision(self) -> int:
        return int(self.read()["revision"])

    def update(
        self,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """在锁内读取、修改并原子写回，返回写入后的完整快照。"""
        with self._locked():
            current = self._read_unlocked()
            actual_revision = int(current["revision"])
            if expected_revision is not None and expected_revision != actual_revision:
                raise VaultConflict(f"凭据已更新（当前 revision={actual_revision}）")
            candidate = copy.deepcopy(current)
            result = mutator(candidate)
            if result is not None:
                candidate = result
            candidate = self._normalise_state(candidate)
            candidate["revision"] = actual_revision + 1
            self._write_unlocked(candidate)
            return copy.deepcopy(candidate)

    def get_provider_state(self, provider_id: str, default: T | None = None) -> dict[str, Any] | T | None:
        state = self.read()
        value = state["providers"].get(provider_id, default)
        return copy.deepcopy(value)

    def replace_provider_state(
        self,
        provider_id: str,
        provider_state: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider_id 不能为空")
        if not isinstance(provider_state, dict):
            raise ValueError("Provider state 必须是对象")

        def apply(state: dict[str, Any]) -> None:
            state["providers"][provider_id] = copy.deepcopy(provider_state)
            return None

        return self.update(apply, expected_revision=expected_revision)

    def update_provider_state(
        self,
        provider_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider_id 不能为空")

        def apply(state: dict[str, Any]) -> None:
            current = copy.deepcopy(state["providers"].get(provider_id, {}))
            result = mutator(current)
            if result is not None:
                current = result
            if not isinstance(current, dict):
                raise ValueError("Provider state 更新结果必须是对象")
            state["providers"][provider_id] = current
            return None

        return self.update(apply, expected_revision=expected_revision)

    def delete_provider_state(self, provider_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        def apply(state: dict[str, Any]) -> None:
            state["providers"].pop(provider_id, None)
            return None

        return self.update(apply, expected_revision=expected_revision)

    def get_global_secret(self, key: str, default: T | None = None) -> Any | T | None:
        value = self.read()["global"].get(key, default)
        return copy.deepcopy(value)

    def set_global_secret(
        self,
        key: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(key, str) or not key:
            raise ValueError("全局 secret key 不能为空")

        def apply(state: dict[str, Any]) -> None:
            state["global"][key] = copy.deepcopy(value)
            return None

        return self.update(apply, expected_revision=expected_revision)

    def clear_global_secret(self, key: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        def apply(state: dict[str, Any]) -> None:
            state["global"].pop(key, None)
            return None

        return self.update(apply, expected_revision=expected_revision)


vault = CredentialVault()


def get_provider_state(provider_id: str, default: T | None = None) -> dict[str, Any] | T | None:
    return vault.get_provider_state(provider_id, default)


def update_provider_state(
    provider_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return vault.update_provider_state(provider_id, mutator, expected_revision=expected_revision)


def get_global_secret(key: str, default: T | None = None) -> Any | T | None:
    return vault.get_global_secret(key, default)


def set_global_secret(key: str, value: Any, *, expected_revision: int | None = None) -> dict[str, Any]:
    return vault.set_global_secret(key, value, expected_revision=expected_revision)


def clear_global_secret(key: str, *, expected_revision: int | None = None) -> dict[str, Any]:
    return vault.clear_global_secret(key, expected_revision=expected_revision)
