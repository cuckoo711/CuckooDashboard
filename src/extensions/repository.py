"""Thread-safe SQLite desired-state storage for discovered extensions."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

_SCHEMA_VERSION = 1


class ExtensionRepositoryError(RuntimeError):
    """Structured base error raised by the extension state repository."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ExtensionRevisionConflictError(ExtensionRepositoryError):
    """A global revision compare-and-swap check did not match."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            "revision_conflict",
            f"extension state revision conflict: expected {expected_revision}, current {current_revision}",
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


# A concise alias for callers that do not need the revision implementation detail.
ExtensionStateConflictError = ExtensionRevisionConflictError


@dataclass(frozen=True)
class ExtensionStateSnapshot:
    """Immutable desired-state mapping observed at one global revision."""

    revision: int
    states: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))

    @property
    def desired(self) -> Mapping[str, bool]:
        """Alias exposing the mapping explicitly as desired states."""

        return self.states


_EMPTY_SNAPSHOT = ExtensionStateSnapshot(0, {})


class ExtensionStateRepository:
    """Lazily connected SQLite repository using global-revision CAS writes.

    Reading a snapshot from a missing on-disk database returns revision zero and
    an empty mapping without creating the database or its parent directory.
    """

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.database = str(database)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connection is not None

    def list_snapshot(self) -> ExtensionStateSnapshot:
        """Return all desired states and their shared revision."""

        with self._lock:
            database_path = self._plain_database_path()
            if self._connection is None and database_path is not None and not database_path.exists():
                return _EMPTY_SNAPSHOT
            try:
                connection = self._connect_locked()
                return self._snapshot_locked(connection)
            except ExtensionRepositoryError:
                raise
            except sqlite3.Error as exc:
                raise ExtensionRepositoryError(
                    "database_error", "failed to read extension desired state"
                ) from exc

    def snapshot(self) -> ExtensionStateSnapshot:
        """Alias for :meth:`list_snapshot`."""

        return self.list_snapshot()

    def set_desired(
        self,
        extension_id: str,
        desired: bool,
        *,
        expected_revision: int,
    ) -> ExtensionStateSnapshot:
        """Set one desired state if the global revision matches.

        Every successful write increments the global revision exactly once and
        returns the complete post-write snapshot.
        """

        if not isinstance(extension_id, str) or not extension_id:
            raise ExtensionRepositoryError("invalid_extension_id", "extension_id must be non-empty")
        if type(desired) is not bool:
            raise ExtensionRepositoryError("invalid_desired_state", "desired must be a boolean")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ExtensionRepositoryError(
                "invalid_revision", "expected_revision must be a non-negative integer"
            )

        with self._lock:
            try:
                with self._transaction_locked() as connection:
                    cursor = connection.execute(
                        """
                        UPDATE extension_meta
                        SET revision = revision + 1
                        WHERE singleton = 1 AND revision = ?
                        """,
                        (expected_revision,),
                    )
                    if cursor.rowcount != 1:
                        current_revision = self._current_revision_locked(connection)
                        raise ExtensionRevisionConflictError(
                            expected_revision=expected_revision,
                            current_revision=current_revision,
                        )
                    next_revision = expected_revision + 1
                    connection.execute(
                        """
                        INSERT INTO extension_states (extension_id, desired, updated_revision)
                        VALUES (?, ?, ?)
                        ON CONFLICT(extension_id) DO UPDATE SET
                            desired = excluded.desired,
                            updated_revision = excluded.updated_revision
                        """,
                        (extension_id, int(desired), next_revision),
                    )
                    return self._snapshot_locked(connection)
            except ExtensionRepositoryError:
                raise
            except sqlite3.Error as exc:
                raise ExtensionRepositoryError(
                    "database_error", "failed to update extension desired state"
                ) from exc

    def close(self) -> None:
        """Close the lazy connection; a later operation may reopen it."""

        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def _plain_database_path(self) -> Path | None:
        if self.database == ":memory:" or self.database.startswith("file:"):
            return None
        return Path(self.database).expanduser()

    def _connect_locked(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        database_path = self._plain_database_path()
        if database_path is not None:
            try:
                database_path.resolve().parent.mkdir(parents=True, exist_ok=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ExtensionRepositoryError(
                    "database_open_error", "extension state database directory cannot be created"
                ) from exc
        try:
            connection = sqlite3.connect(
                self.database,
                timeout=self.busy_timeout_ms / 1000,
                check_same_thread=False,
                isolation_level=None,
                uri=self.database.startswith("file:"),
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema(connection)
        except ExtensionRepositoryError:
            if "connection" in locals():
                connection.close()
            raise
        except sqlite3.Error as exc:
            if "connection" in locals():
                connection.close()
            raise ExtensionRepositoryError(
                "database_open_error", "extension state database cannot be opened"
            ) from exc
        self._connection = connection
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version not in {0, _SCHEMA_VERSION}:
            raise ExtensionRepositoryError(
                "unsupported_schema",
                f"unsupported extension state schema: {current_version}",
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS extension_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
                revision INTEGER NOT NULL CHECK (revision >= 0)
            );

            CREATE TABLE IF NOT EXISTS extension_states (
                extension_id TEXT PRIMARY KEY,
                desired INTEGER NOT NULL CHECK (desired IN (0, 1)),
                updated_revision INTEGER NOT NULL CHECK (updated_revision >= 1)
            );

            INSERT OR IGNORE INTO extension_meta (singleton, schema_version, revision)
            VALUES (1, 1, 0);
            """
        )
        meta = connection.execute(
            "SELECT schema_version FROM extension_meta WHERE singleton = 1"
        ).fetchone()
        if meta is None or int(meta["schema_version"]) != _SCHEMA_VERSION:
            raise ExtensionRepositoryError(
                "unsupported_schema", "unsupported extension metadata schema"
            )
        if current_version == 0:
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @contextmanager
    def _transaction_locked(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect_locked()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def _current_revision_locked(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT revision FROM extension_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise ExtensionRepositoryError(
                "corrupt_database", "extension metadata row is missing"
            )
        return int(row["revision"])

    @classmethod
    def _snapshot_locked(cls, connection: sqlite3.Connection) -> ExtensionStateSnapshot:
        revision = cls._current_revision_locked(connection)
        rows = connection.execute(
            "SELECT extension_id, desired FROM extension_states ORDER BY extension_id"
        ).fetchall()
        return ExtensionStateSnapshot(
            revision,
            {str(row["extension_id"]): bool(row["desired"]) for row in rows},
        )


# Shorter public name for consumers that already operate in the extensions package.
ExtensionRepository = ExtensionStateRepository
