"""Workspace recovery behavior when an extension is unavailable."""

from __future__ import annotations

from contracts.workspace import (
    WidgetConstraints,
    WidgetInstance,
    WidgetLayout,
    WorkspaceDefinition,
    WorkspaceGrid,
)
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.repository import WorkspaceRepository
from workspaces.service import WorkspaceService


def test_orphan_extension_widget_remains_readable_movable_and_removable():
    registry = create_builtin_workspace_registry()
    repository = WorkspaceRepository(":memory:")
    service = WorkspaceService(
        repository,
        registry,
        seed_workspace=registry.get_workspace("main"),
    )
    service.list_workspaces()
    repository.create_workspace(
        WorkspaceDefinition(
            id="orphan",
            version=2,
            revision=1,
            name="Orphan",
            kind="custom",
            required=False,
            grid=WorkspaceGrid(),
            widgets=(
                WidgetInstance(
                    id="missing-card",
                    type="com.example.missing.card",
                    slot="main",
                    layout=WidgetLayout(1, 2, 4, 3),
                    constraints=WidgetConstraints(4, 3, 8, 6),
                    owner="com.example.missing",
                ),
            ),
        )
    )

    manifest = service.serialize("orphan")
    widget = manifest["widgets"][0]
    assert widget["available"] is False
    assert widget["owner"] == "com.example.missing"
    assert widget["layout"] == {"x": 1, "y": 2, "width": 4, "height": 3}
    assert service.serialize_summary(service.get("orphan"))["unavailable_widget_count"] == 1

    widget["layout"] = {"x": 3, "y": 4, "width": 4, "height": 3}
    moved = service.update(
        "orphan",
        {
            "revision": manifest["revision"],
            "name": "Orphan",
            "grid": manifest["grid"],
            "widgets": [widget],
        },
    )
    assert moved.revision == 2
    assert service.serialize(moved)["widgets"][0]["layout"]["x"] == 3

    removed = service.update(
        "orphan",
        {
            "revision": moved.revision,
            "name": "Orphan",
            "grid": {"columns": 16, "rows": 15},
            "widgets": [],
        },
    )
    assert removed.revision == 3
    assert service.serialize(removed)["widgets"] == []
