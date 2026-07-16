"""Workspace registry, built-in manifest and API tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.factory import create_app
from contracts.workspace import (
    DataSourceDescriptor,
    WidgetDefinition,
    WidgetInstance,
    WorkspaceDefinition,
)
from features.dashboard.service import get_dashboard_data
from services.github_service import get_github_data
from services.media_service import get_media_info
from services.system_service import get_system_info
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import WorkspaceRegistry


def test_data_source_definition_is_immutable_and_registry_is_per_instance():
    descriptor = DataSourceDescriptor(
        id="test.source",
        kind="snapshot",
        legacy_message_type="test",
        default_interval_seconds=5,
    )
    definition = DataSourceDefinition(descriptor=descriptor, getter=lambda: {"ok": True})
    first = WorkspaceRegistry()
    second = WorkspaceRegistry()
    first.register_data_source(definition)

    assert first.get_data_source("test.source") is definition
    assert first.data_source_ids() == ("test.source",)
    assert list(first.iter_data_sources()) == [definition]
    assert second.data_source_ids() == ()
    with pytest.raises(KeyError):
        second.get_data_source("test.source")
    with pytest.raises(FrozenInstanceError):
        descriptor.kind = "push"
    with pytest.raises(FrozenInstanceError):
        definition.getter = lambda: None


def test_registry_serialization_returns_json_friendly_copies():
    registry = WorkspaceRegistry()
    registry.register_data_source(
        DataSourceDefinition(
            descriptor=DataSourceDescriptor(
                id="test.source",
                kind="snapshot",
                legacy_message_type="test",
                default_interval_seconds=5,
            ),
            getter=lambda: {},
        )
    )
    registry.register_widget(
        WidgetDefinition(
            type="test.widget",
            title="Test Widget",
            sources=("test.source",),
            channels=("test.channel",),
        )
    )
    registry.register_workspace(
        WorkspaceDefinition(
            id="test",
            version=3,
            required=False,
            widgets=(WidgetInstance("test-widget", "test.widget", "main"),),
        )
    )

    payload = registry.serialize_workspace("test")
    payload["sources"][0]["id"] = "mutated"
    payload["widgets"][0]["sources"].append("mutated")

    fresh = registry.serialize_workspace("test")
    assert fresh == {
        "id": "test",
        "version": 3,
        "required": False,
        "sources": [
            {
                "id": "test.source",
                "kind": "snapshot",
                "legacy_message_type": "test",
                "default_interval_seconds": 5,
                "active_interval_seconds": None,
            }
        ],
        "widgets": [
            {
                "id": "test-widget",
                "type": "test.widget",
                "slot": "main",
                "sources": ["test.source"],
                "channels": ["test.channel"],
            }
        ],
    }


def test_widget_types_are_separate_from_workspace_instances():
    registry = WorkspaceRegistry()
    registry.register_data_source(
        DataSourceDefinition(
            descriptor=DataSourceDescriptor(
                id="test.source",
                kind="snapshot",
                legacy_message_type="test",
                default_interval_seconds=1,
            ),
            getter=lambda: {},
        )
    )
    registry.register_widget(
        WidgetDefinition(
            type="test.reusable",
            title="Reusable",
            sources=("test.source",),
        )
    )
    registry.register_workspace(
        WorkspaceDefinition(
            id="multi",
            version=1,
            required=False,
            widgets=(
                WidgetInstance("first", "test.reusable", "left"),
                WidgetInstance("second", "test.reusable", "right"),
            ),
        )
    )

    payload = registry.serialize_workspace("multi")
    assert [widget["id"] for widget in payload["widgets"]] == ["first", "second"]
    assert all(widget["sources"] == ["test.source"] for widget in payload["widgets"])


def test_registry_rejects_unknown_sources_duplicate_instances_and_singletons():
    registry = WorkspaceRegistry()
    registry.register_data_source(
        DataSourceDefinition(
            descriptor=DataSourceDescriptor(
                id="test.source",
                kind="snapshot",
                legacy_message_type="test",
                default_interval_seconds=1,
            ),
            getter=lambda: {},
        )
    )
    with pytest.raises(ValueError, match="unknown data source"):
        registry.register_widget(
            WidgetDefinition(
                type="test.invalid",
                title="Invalid",
                sources=("missing.source",),
            )
        )

    registry.register_widget(
        WidgetDefinition(
            type="test.singleton",
            title="Singleton",
            sources=("test.source",),
            single_instance=True,
        )
    )
    with pytest.raises(ValueError, match="single-instance"):
        registry.register_workspace(
            WorkspaceDefinition(
                id="duplicate-singleton",
                version=1,
                required=False,
                widgets=(
                    WidgetInstance("first", "test.singleton", "left"),
                    WidgetInstance("second", "test.singleton", "right"),
                ),
            )
        )
    with pytest.raises(ValueError, match="duplicate widget id"):
        registry.register_workspace(
            WorkspaceDefinition(
                id="duplicate-id",
                version=1,
                required=False,
                widgets=(
                    WidgetInstance("same", "test.singleton", "left"),
                    WidgetInstance("same", "test.singleton", "right"),
                ),
            )
        )


def test_builtin_registry_contains_required_main_workspace():
    registry = create_builtin_workspace_registry()
    sources = {definition.descriptor.id: definition for definition in registry.iter_data_sources()}

    assert set(sources) == {
        "system.snapshot",
        "media.playback",
        "github.contributions",
        "dashboard.aggregate",
    }
    assert sources["system.snapshot"].getter is get_system_info
    assert sources["media.playback"].getter is get_media_info
    assert sources["github.contributions"].getter is get_github_data
    assert sources["dashboard.aggregate"].getter is get_dashboard_data
    assert sources["dashboard.aggregate"].descriptor.default_interval_seconds == 60
    assert sources["dashboard.aggregate"].descriptor.active_interval_seconds == 20

    workspace = registry.get_workspace("main")
    assert workspace.required is True
    assert workspace.sources == ("dashboard.aggregate",)
    assert {widget.id for widget in workspace.widgets} == {
        "system-info",
        "system-network",
        "system-uptime",
        "system-disks",
        "media-player",
        "github-contributions",
    }
    assert {widget.type for widget in workspace.widgets} == {
        "builtin.system.info",
        "builtin.system.network",
        "builtin.system.uptime",
        "builtin.system.disks",
        "builtin.media.player",
        "builtin.github.contributions",
    }
    system_instances = [
        widget for widget in workspace.widgets if widget.type.startswith("builtin.system.")
    ]
    assert len(system_instances) == 4
    assert all(
        registry.get_widget(instance.type).sources == ("system.snapshot",)
        for instance in system_instances
    )
    player_instance = next(
        widget for widget in workspace.widgets if widget.type == "builtin.media.player"
    )
    player = registry.get_widget(player_instance.type)
    assert player.sources == ("media.playback",)
    assert player.channels == ("media.lyric",)
    assert player.single_instance is True

    manifest = registry.serialize_workspace("main")
    assert manifest["id"] == "main"
    assert manifest["version"] == 1
    assert manifest["required"] is True
    assert {source["id"] for source in manifest["sources"]} == set(sources)


def test_workspace_manifest_route_uses_runtime_registry_and_returns_json_404():
    app = create_app({"TESTING": True})
    registry = app.extensions["workspace_registry"]
    client = app.test_client()

    response = client.get("/api/workspaces/main")
    assert response.status_code == 200
    assert response.get_json() == registry.serialize_workspace("main")
    assert response.headers["Cache-Control"] == "no-store"

    missing = client.get("/api/workspaces/unknown")
    assert missing.status_code == 404
    assert missing.headers["Cache-Control"] == "no-store"
    assert missing.get_json() == {
        "error": "workspace_not_found",
        "workspace_id": "unknown",
    }
