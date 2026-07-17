"""Manifest v2 persistence, service and Settings CRUD tests."""

from __future__ import annotations

import sqlite3

import pytest

from app.factory import create_app
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.repository import (
    RequiredWorkspaceError,
    WorkspaceConflictError,
    WorkspaceRepository,
)
from workspaces.service import WorkspaceService, WorkspaceValidationError


LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}
ORIGIN = {"Origin": "http://localhost"}


def _service(database=":memory:"):
    registry = create_builtin_workspace_registry()
    main = registry.get_workspace("main")
    repository = WorkspaceRepository(database)
    return WorkspaceService(repository, registry, seed_workspace=main)


def test_builtin_manifest_v2_has_fixed_grid_catalog_and_layout():
    service = _service()
    manifest = service.serialize("main")

    assert manifest["version"] == 2
    assert manifest["revision"] == 1
    assert manifest["name"] == "Main Dashboard"
    assert manifest["kind"] == "builtin"
    assert manifest["required"] is True
    assert manifest["grid"] == {"columns": 16, "rows": 15}
    assert [widget["id"] for widget in manifest["widgets"]] == [
        "system-info",
        "network",
        "uptime",
        "disks",
        "token-card",
        "player",
        "github",
    ]
    assert [widget["type"] for widget in manifest["widgets"]] == [
        "builtin.dashboard.system-info",
        "builtin.dashboard.network",
        "builtin.dashboard.uptime",
        "builtin.dashboard.disks",
        "builtin.dashboard.vibe",
        "builtin.dashboard.player",
        "builtin.dashboard.github",
    ]
    assert manifest["widgets"][0]["layout"] == {"x": 0, "y": 0, "width": 6, "height": 5}
    assert manifest["widgets"][4]["constraints"]["min_width"] == 6
    assert len(service.widget_catalog()) == 7


def test_repository_is_lazy_configures_sqlite_and_reopens(tmp_path):
    database = tmp_path / "workspaces.db"
    repository = WorkspaceRepository(database, busy_timeout_ms=2345)
    assert repository.connected is False

    assert repository.list_workspaces() == ()
    assert repository.connected is True
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"workspaces", "workspace_widgets"} <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        widget_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workspace_widgets)")
        }
        assert "owner_id" in widget_columns
    finally:
        connection.close()

    with repository._lock:
        live = repository._connect_locked()
        assert live.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert live.execute("PRAGMA busy_timeout").fetchone()[0] == 2345
    repository.close()
    assert repository.connected is False
    assert repository.list_workspaces() == ()


def test_schema_v1_migrates_existing_widgets_to_core_owner(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            required INTEGER NOT NULL, revision INTEGER NOT NULL,
            version INTEGER NOT NULL, grid_columns INTEGER NOT NULL,
            grid_rows INTEGER NOT NULL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE workspace_widgets (
            workspace_id TEXT NOT NULL, position INTEGER NOT NULL,
            widget_id TEXT NOT NULL, type TEXT NOT NULL, slot TEXT NOT NULL,
            layout_x INTEGER NOT NULL, layout_y INTEGER NOT NULL,
            layout_width INTEGER NOT NULL, layout_height INTEGER NOT NULL,
            min_width INTEGER NOT NULL, min_height INTEGER NOT NULL,
            max_width INTEGER NOT NULL, max_height INTEGER NOT NULL,
            PRIMARY KEY (workspace_id, widget_id), UNIQUE (workspace_id, position)
        );
        INSERT INTO workspaces
            (id, name, kind, required, revision, version, grid_columns, grid_rows)
        VALUES ('legacy', 'Legacy', 'custom', 0, 1, 2, 16, 15);
        INSERT INTO workspace_widgets
            (workspace_id, position, widget_id, type, slot,
             layout_x, layout_y, layout_width, layout_height,
             min_width, min_height, max_width, max_height)
        VALUES ('legacy', 0, 'system-info', 'builtin.dashboard.system-info', 'main',
                0, 0, 6, 5, 4, 4, 16, 15);
        PRAGMA user_version = 1;
        """
    )
    connection.commit()
    connection.close()

    workspace = WorkspaceRepository(database).get_workspace("legacy")

    assert workspace.widgets[0].owner == "cuckoo.core.dashboard"
    migrated = sqlite3.connect(database)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        migrated.close()


def test_seed_create_update_cas_delete_and_required_protection():
    service = _service()
    assert [item["id"] for item in service.list_workspaces()] == ["main"]
    assert service.repository.seed_workspace(service.registry.get_workspace("main")) is False

    created = service.create_blank("Secondary", workspace_id="secondary")
    assert created.revision == 1
    updated = service.update(
        "secondary",
        {"revision": 1, "name": "Renamed"},
    )
    assert updated.revision == 2
    assert updated.name == "Renamed"

    with pytest.raises(WorkspaceConflictError) as stale:
        service.update("secondary", {"revision": 1, "name": "Stale"})
    assert stale.value.current_revision == 2
    with pytest.raises(RequiredWorkspaceError):
        service.delete("main")

    deleted = service.delete("secondary", expected_revision=2)
    assert deleted.id == "secondary"
    assert [item["id"] for item in service.list_workspaces()] == ["main"]


def test_service_rejects_unknown_overlap_and_out_of_bounds_widgets():
    service = _service()
    manifest = service.serialize("main")
    manifest["widgets"][1]["layout"] = dict(manifest["widgets"][0]["layout"])
    with pytest.raises(WorkspaceValidationError, match="overlaps"):
        service.update("main", manifest)

    manifest = service.serialize("main")
    manifest["widgets"][0]["type"] = "missing.widget"
    with pytest.raises(WorkspaceValidationError, match="unknown widget"):
        service.update("main", manifest)

    manifest = service.serialize("main")
    manifest["widgets"][0]["layout"]["x"] = 15
    with pytest.raises(WorkspaceValidationError, match="exceeds"):
        service.update("main", manifest)

    manifest = service.serialize("main")
    manifest["widgets"][0]["constraints"]["min_width"] = 1
    with pytest.raises(WorkspaceValidationError, match="constraints cannot be changed"):
        service.update("main", manifest)

    manifest = service.serialize("main")
    manifest["kind"] = "custom"
    with pytest.raises(WorkspaceValidationError, match="kind cannot be changed"):
        service.update("main", manifest)

    manifest = service.serialize("main")
    manifest["required"] = False
    with pytest.raises(WorkspaceValidationError, match="required flag cannot be changed"):
        service.update("main", manifest)


def test_removing_vibe_removes_dashboard_aggregate_subscription():
    service = _service()
    manifest = service.serialize("main")
    manifest["widgets"] = [
        widget for widget in manifest["widgets"]
        if widget["type"] != "builtin.dashboard.vibe"
    ]

    updated = service.update("main", manifest)
    serialized = service.serialize(updated)

    assert "dashboard.aggregate" not in {source["id"] for source in serialized["sources"]}


def test_public_and_settings_workspace_routes_cover_crud_security_and_conflicts(monkeypatch):
    app = create_app({"TESTING": True})
    client = app.test_client()
    broadcasts = []
    monkeypatch.setattr(app.extensions["dashboard_runtime"].hub, "broadcast", broadcasts.append)

    public_list = client.get("/api/workspaces")
    assert public_list.status_code == 200
    assert public_list.get_json()["workspaces"][0]["id"] == "main"
    assert client.get("/api/workspaces/main").get_json()["version"] == 2
    assert client.get("/workspaces/main").status_code == 200
    assert client.get("/workspaces/missing").status_code == 404

    assert client.get(
        "/api/settings/workspaces", environ_base={"REMOTE_ADDR": "10.0.0.8"}
    ).status_code == 403
    collection = client.get("/api/settings/workspaces", environ_base=LOOPBACK)
    assert collection.status_code == 200
    assert collection.get_json()["grid"] == {"columns": 16, "rows": 15}
    assert len(collection.get_json()["widget_catalog"]) == 7

    assert client.post(
        "/api/settings/workspaces",
        json={"name": "Blocked"},
        environ_base=LOOPBACK,
    ).status_code == 403
    invalid = client.post(
        "/api/settings/workspaces",
        json={},
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "workspace_validation_error"

    created = client.post(
        "/api/settings/workspaces",
        json={"id": "secondary", "name": "Secondary"},
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert created.status_code == 201
    assert created.get_json()["revision"] == 1

    updated = client.put(
        "/api/settings/workspaces/secondary",
        json={"revision": 1, "name": "Renamed"},
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert updated.status_code == 200
    assert updated.get_json()["revision"] == 2
    stale = client.put(
        "/api/settings/workspaces/secondary",
        json={"revision": 1, "name": "Stale"},
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert stale.status_code == 409
    assert stale.get_json()["error"]["current_revision"] == 2
    stale_delete = client.delete(
        "/api/settings/workspaces/secondary?revision=1",
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert stale_delete.status_code == 409
    assert stale_delete.get_json()["error"]["current_revision"] == 2

    copied = client.post(
        "/api/settings/workspaces/secondary/duplicate",
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert copied.status_code == 201
    copy_id = copied.get_json()["id"]
    assert copy_id != "secondary"

    protected = client.delete(
        "/api/settings/workspaces/main",
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert protected.status_code == 409
    assert protected.get_json()["error"]["code"] == "required_workspace"
    missing_revision = client.delete(
        f"/api/settings/workspaces/{copy_id}",
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert missing_revision.status_code == 400
    assert missing_revision.get_json()["error"]["field"] == "revision"
    deleted = client.delete(
        f"/api/settings/workspaces/{copy_id}?revision=1",
        headers=ORIGIN,
        environ_base=LOOPBACK,
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["ok"] is True
    assert [message["type"] for message in broadcasts] == [
        "workspace_updated",
        "workspace_updated",
        "workspace_updated",
        "workspace_updated",
    ]


def test_testing_apps_use_independent_memory_databases_and_close_reseed():
    first = create_app({"TESTING": True})
    second = create_app({"TESTING": True})
    first_service = first.extensions["workspace_service"]
    second_service = second.extensions["workspace_service"]

    first_service.create_blank("Only First", workspace_id="only-first")
    assert {item["id"] for item in first_service.list_workspaces()} == {"main", "only-first"}
    assert {item["id"] for item in second_service.list_workspaces()} == {"main"}

    first_service.close()
    assert first.extensions["workspace_repository"].connected is False
    assert {item["id"] for item in first_service.list_workspaces()} == {"main"}
