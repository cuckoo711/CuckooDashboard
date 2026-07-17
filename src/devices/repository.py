"""SQLite persistence for browser-backed display terminals."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class DeviceRepository:
    """Thread-safe, lazily connected store for trusted display terminals."""

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.database = str(database)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def _connect_locked(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if self.database != ":memory:" and not self.database.startswith("file:"):
            Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS display_devices (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'disabled')),
                workspace_id TEXT NOT NULL DEFAULT 'main',
                scale_mode TEXT NOT NULL DEFAULT 'auto'
                    CHECK (scale_mode IN ('auto', 'fixed')),
                scale REAL NOT NULL DEFAULT 1.0 CHECK (scale BETWEEN 0.25 AND 4.0),
                layout_override TEXT NOT NULL DEFAULT '{}',
                note TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_page TEXT NOT NULL DEFAULT '',
                last_viewport TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_display_devices_status_seen "
            "ON display_devices(status, last_seen_at DESC)"
        )
        self._connection = connection
        return connection

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
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            layout_override = json.loads(str(row["layout_override"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            layout_override = {}
        try:
            last_viewport = json.loads(str(row["last_viewport"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            last_viewport = {}
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "workspace_id": str(row["workspace_id"]),
            "scale_mode": str(row["scale_mode"]),
            "scale": float(row["scale"]),
            "layout_override": layout_override if isinstance(layout_override, dict) else {},
            "note": str(row["note"] or ""),
            "display_name": str(row["display_name"] or ""),
            "first_seen_at": str(row["first_seen_at"]),
            "last_seen_at": str(row["last_seen_at"]),
            "last_page": str(row["last_page"] or ""),
            "last_viewport": last_viewport if isinstance(last_viewport, dict) else {},
        }

    def close(self) -> None:
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connect_locked().execute(
                "SELECT * FROM display_devices WHERE id = ?", (device_id,)
            ).fetchone()
            return self._payload(row) if row is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connect_locked().execute(
                "SELECT * FROM display_devices "
                "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, "
                "last_seen_at DESC, id"
            ).fetchall()
            return [self._payload(row) for row in rows]

    def register_or_touch(
        self,
        device_id: str,
        *,
        display_name: str = "",
        page: str = "",
        viewport: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        viewport_json = json.dumps(viewport or {}, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._transaction_locked() as connection:
            existing = connection.execute(
                "SELECT 1 FROM display_devices WHERE id = ?", (device_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO display_devices
                        (id, display_name, last_page, last_viewport)
                    VALUES (?, ?, ?, ?)
                    """,
                    (device_id, display_name, page, viewport_json),
                )
            else:
                connection.execute(
                    """
                    UPDATE display_devices
                    SET display_name = CASE WHEN ? <> '' THEN ? ELSE display_name END,
                        last_page = ?, last_viewport = ?, last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (display_name, display_name, page, viewport_json, device_id),
                )
            row = connection.execute(
                "SELECT * FROM display_devices WHERE id = ?", (device_id,)
            ).fetchone()
            assert row is not None
            return self._payload(row)

    def update(self, device_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "workspace_id", "scale_mode", "scale", "layout_override", "note", "display_name",
        }
        fields = {key: value for key, value in changes.items() if key in allowed}
        if not fields:
            return self.get(device_id)
        encoded = dict(fields)
        if "layout_override" in encoded:
            encoded["layout_override"] = json.dumps(encoded["layout_override"], ensure_ascii=False, separators=(",", ":"))
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._lock, self._transaction_locked() as connection:
            cursor = connection.execute(
                f"UPDATE display_devices SET {assignments} WHERE id = ?",
                (*encoded.values(), device_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM display_devices WHERE id = ?", (device_id,)
            ).fetchone()
            assert row is not None
            return self._payload(row)

    def delete(self, device_id: str) -> dict[str, Any] | None:
        with self._lock, self._transaction_locked() as connection:
            row = connection.execute(
                "SELECT * FROM display_devices WHERE id = ?", (device_id,)
            ).fetchone()
            if row is None:
                return None
            payload = self._payload(row)
            cursor = connection.execute(
                "DELETE FROM display_devices WHERE id = ?", (device_id,)
            )
            if cursor.rowcount != 1:
                return None
            return payload

