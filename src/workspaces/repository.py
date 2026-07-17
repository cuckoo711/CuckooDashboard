"""SQLite persistence for editable workspace manifests."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from contracts.workspace import (
    WidgetConstraints,
    WidgetInstance,
    WidgetLayout,
    WorkspaceDefinition,
    WorkspaceGrid,
)

_SCHEMA_VERSION = 2
_CORE_OWNER_ID = "cuckoo.core.dashboard"


class WorkspaceRepositoryError(RuntimeError):
    """Base repository failure."""


class WorkspaceNotFoundError(WorkspaceRepositoryError):
    """Requested workspace does not exist."""


class WorkspaceConflictError(WorkspaceRepositoryError):
    """A create or compare-and-swap revision check failed."""

    def __init__(self, message: str, *, current_revision: int | None = None) -> None:
        super().__init__(message)
        self.current_revision = current_revision


class RequiredWorkspaceError(WorkspaceConflictError):
    """A required workspace cannot be deleted."""


class WorkspaceRepository:
    """Thread-safe, lazily connected SQLite workspace store."""

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.database = str(database)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connection is not None

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
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema(connection)
        self._connection = connection
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version not in {0, 1, _SCHEMA_VERSION}:
            raise WorkspaceRepositoryError(
                f"unsupported workspace database schema: {current_version}"
            )
        connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0, 1)),
                revision INTEGER NOT NULL CHECK (revision >= 1),
                version INTEGER NOT NULL DEFAULT 2 CHECK (version >= 2),
                grid_columns INTEGER NOT NULL DEFAULT 16 CHECK (grid_columns > 0),
                grid_rows INTEGER NOT NULL DEFAULT 15 CHECK (grid_rows > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS workspace_widgets (
                workspace_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                widget_id TEXT NOT NULL,
                type TEXT NOT NULL,
                slot TEXT NOT NULL,
                owner_id TEXT NOT NULL DEFAULT '{_CORE_OWNER_ID}',
                layout_x INTEGER NOT NULL CHECK (layout_x >= 0),
                layout_y INTEGER NOT NULL CHECK (layout_y >= 0),
                layout_width INTEGER NOT NULL CHECK (layout_width > 0),
                layout_height INTEGER NOT NULL CHECK (layout_height > 0),
                min_width INTEGER NOT NULL CHECK (min_width > 0),
                min_height INTEGER NOT NULL CHECK (min_height > 0),
                max_width INTEGER NOT NULL CHECK (max_width > 0),
                max_height INTEGER NOT NULL CHECK (max_height > 0),
                PRIMARY KEY (workspace_id, widget_id),
                UNIQUE (workspace_id, position),
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            """
        )
        if current_version == 1:
            connection.execute(
                "ALTER TABLE workspace_widgets ADD COLUMN owner_id TEXT NOT NULL "
                f"DEFAULT '{_CORE_OWNER_ID}'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_widgets_owner "
            "ON workspace_widgets(owner_id, workspace_id)"
        )
        if current_version < _SCHEMA_VERSION:
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

    def close(self) -> None:
        """Close the lazy connection; later operations transparently reopen it."""
        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def list_workspaces(self) -> tuple[WorkspaceDefinition, ...]:
        with self._lock:
            connection = self._connect_locked()
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY required DESC, name COLLATE NOCASE, id"
            ).fetchall()
            return tuple(self._row_to_workspace(connection, row) for row in rows)

    def get_workspace(self, workspace_id: str) -> WorkspaceDefinition:
        with self._lock:
            connection = self._connect_locked()
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFoundError(workspace_id)
            return self._row_to_workspace(connection, row)

    def seed_workspace(self, workspace: WorkspaceDefinition) -> bool:
        """Insert a required built-in workspace once without overwriting user state."""
        with self._lock, self._transaction_locked() as connection:
            exists = connection.execute(
                "SELECT 1 FROM workspaces WHERE id = ?", (workspace.id,)
            ).fetchone()
            if exists is not None:
                return False
            self._insert_workspace(connection, workspace)
            return True

    def create_workspace(self, workspace: WorkspaceDefinition) -> WorkspaceDefinition:
        created = replace(workspace, version=2, revision=1)
        with self._lock, self._transaction_locked() as connection:
            try:
                self._insert_workspace(connection, created)
            except sqlite3.IntegrityError as exc:
                raise WorkspaceConflictError(f"workspace already exists: {created.id}") from exc
        return created

    def update_workspace(
        self,
        workspace: WorkspaceDefinition,
        *,
        expected_revision: int,
    ) -> WorkspaceDefinition:
        next_workspace = replace(workspace, version=2, revision=expected_revision + 1)
        with self._lock, self._transaction_locked() as connection:
            cursor = connection.execute(
                """
                UPDATE workspaces
                SET name = ?, kind = ?, required = ?, revision = ?, version = ?,
                    grid_columns = ?, grid_rows = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND revision = ?
                """,
                (
                    next_workspace.name,
                    next_workspace.kind,
                    int(next_workspace.required),
                    next_workspace.revision,
                    next_workspace.version,
                    next_workspace.grid.columns if next_workspace.grid else 16,
                    next_workspace.grid.rows if next_workspace.grid else 15,
                    next_workspace.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT revision FROM workspaces WHERE id = ?", (workspace.id,)
                ).fetchone()
                if row is None:
                    raise WorkspaceNotFoundError(workspace.id)
                raise WorkspaceConflictError(
                    "workspace revision conflict",
                    current_revision=int(row["revision"]),
                )
            connection.execute(
                "DELETE FROM workspace_widgets WHERE workspace_id = ?", (workspace.id,)
            )
            self._insert_widgets(connection, next_workspace)
        return next_workspace

    def delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> WorkspaceDefinition:
        with self._lock, self._transaction_locked() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFoundError(workspace_id)
            current = self._row_to_workspace(connection, row)
            if current.required or current.id == "main":
                raise RequiredWorkspaceError("required workspace cannot be deleted")
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkspaceConflictError(
                    "workspace revision conflict",
                    current_revision=current.revision,
                )
            cursor = connection.execute(
                "DELETE FROM workspaces WHERE id = ? AND revision = ?",
                (workspace_id, current.revision),
            )
            if cursor.rowcount != 1:
                raise WorkspaceConflictError("workspace revision conflict")
            return current

    def list_widget_references(self, owner_id: str) -> list[dict[str, object]]:
        """Return persisted workspace instances owned by one extension package."""
        with self._lock:
            connection = self._connect_locked()
            rows = connection.execute(
                """
                SELECT w.id AS workspace_id, w.name AS workspace_name,
                       ww.widget_id, ww.type
                FROM workspace_widgets AS ww
                JOIN workspaces AS w ON w.id = ww.workspace_id
                WHERE ww.owner_id = ?
                ORDER BY w.required DESC, w.name COLLATE NOCASE, ww.position
                """,
                (owner_id,),
            ).fetchall()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            workspace_id = str(row["workspace_id"])
            entry = grouped.setdefault(
                workspace_id,
                {
                    "workspace_id": workspace_id,
                    "workspace_name": str(row["workspace_name"]),
                    "widgets": [],
                },
            )
            widgets = entry["widgets"]
            assert isinstance(widgets, list)
            widgets.append(
                {"id": str(row["widget_id"]), "type": str(row["type"])}
            )
        return list(grouped.values())

    @staticmethod
    def _insert_workspace(connection: sqlite3.Connection, workspace: WorkspaceDefinition) -> None:
        grid = workspace.grid or WorkspaceGrid()
        connection.execute(
            """
            INSERT INTO workspaces
                (id, name, kind, required, revision, version, grid_columns, grid_rows)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace.id,
                workspace.name or workspace.id,
                workspace.kind or "dashboard",
                int(workspace.required),
                workspace.revision or 1,
                max(2, workspace.version),
                grid.columns,
                grid.rows,
            ),
        )
        WorkspaceRepository._insert_widgets(connection, workspace)

    @staticmethod
    def _insert_widgets(connection: sqlite3.Connection, workspace: WorkspaceDefinition) -> None:
        for position, widget in enumerate(workspace.widgets):
            layout = widget.layout or WidgetLayout(0, 0, 1, 1)
            constraints = widget.constraints or WidgetConstraints()
            connection.execute(
                """
                INSERT INTO workspace_widgets
                    (workspace_id, position, widget_id, type, slot, owner_id,
                     layout_x, layout_y, layout_width, layout_height,
                     min_width, min_height, max_width, max_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace.id,
                    position,
                    widget.id,
                    widget.type,
                    widget.slot,
                    widget.owner or _CORE_OWNER_ID,
                    layout.x,
                    layout.y,
                    layout.width,
                    layout.height,
                    constraints.min_width,
                    constraints.min_height,
                    constraints.max_width,
                    constraints.max_height,
                ),
            )

    @staticmethod
    def _row_to_workspace(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> WorkspaceDefinition:
        widget_rows = connection.execute(
            """
            SELECT * FROM workspace_widgets
            WHERE workspace_id = ? ORDER BY position
            """,
            (row["id"],),
        ).fetchall()
        widgets = tuple(
            WidgetInstance(
                id=widget["widget_id"],
                type=widget["type"],
                slot=widget["slot"],
                layout=WidgetLayout(
                    widget["layout_x"],
                    widget["layout_y"],
                    widget["layout_width"],
                    widget["layout_height"],
                ),
                constraints=WidgetConstraints(
                    widget["min_width"],
                    widget["min_height"],
                    widget["max_width"],
                    widget["max_height"],
                ),
                owner=widget["owner_id"],
            )
            for widget in widget_rows
        )
        return WorkspaceDefinition(
            id=row["id"],
            version=row["version"],
            revision=row["revision"],
            name=row["name"],
            kind=row["kind"],
            required=bool(row["required"]),
            grid=WorkspaceGrid(row["grid_columns"], row["grid_rows"]),
            widgets=widgets,
        )
