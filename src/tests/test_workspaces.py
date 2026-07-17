"""Workspace registry, built-in manifest and API tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.factory import create_app
from contracts.extension import ExtensionContributions
from contracts.workspace import (
    DataSourceDescriptor,
    DataSourceRefreshPolicy,
    WidgetConstraints,
    WidgetDefinition,
    WidgetInstance,
    WidgetLayout,
    WorkspaceDefinition,
    WorkspaceGrid,
)
from features.dashboard.service import get_dashboard_data
from services.github_service import get_github_data
from services.media_service import get_media_info
from services.system_service import get_system_info
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import RegistryOwner, WorkspaceRegistry


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


def test_refresh_policy_is_immutable_normalized_and_serialized_additively():
    policy = DataSourceRefreshPolicy(
        supports_push=True,
        default_interval_ms=2500,
        active_interval_ms=500,
        minimum_interval_ms=250,
        cache_ttl_ms=750,
        pause_without_subscribers=False,
        stale_if_error_ms=5000,
        error_backoff_initial_ms=1000,
        error_backoff_max_ms=8000,
    )
    descriptor = DataSourceDescriptor(
        id="test.normalized",
        kind="snapshot",
        legacy_message_type=None,
        default_interval_seconds=2.5,
        active_interval_seconds=0.5,
        refresh_policy=policy,
    )

    with pytest.raises(ValueError, match="legacy default interval differs"):
        DataSourceDescriptor(
            id="test.conflict",
            kind="snapshot",
            legacy_message_type=None,
            default_interval_seconds=99,
            active_interval_seconds=0.5,
            refresh_policy=policy,
        )

    assert descriptor.default_interval_seconds == 2.5
    assert descriptor.active_interval_seconds == 0.5
    assert descriptor.to_payload()["refresh_policy"] == policy.to_payload()
    assert DataSourceDescriptor.from_payload(descriptor.to_payload()) == descriptor
    with pytest.raises(FrozenInstanceError):
        policy.default_interval_ms = 10


def test_registry_strictly_validates_refresh_policy_and_legacy_owner_rules():
    invalid_registry = WorkspaceRegistry()
    invalid = DataSourceDefinition(
        descriptor=DataSourceDescriptor(
            id="test.invalid-policy",
            kind="snapshot",
            legacy_message_type=None,
            default_interval_seconds=1,
            refresh_policy=DataSourceRefreshPolicy(
                default_interval_ms=1000,
                minimum_interval_ms=1001,
            ),
        ),
        getter=lambda: {},
    )
    with pytest.raises(ValueError, match="minimum interval exceeds default"):
        invalid_registry.register_data_source(invalid)

    registry = WorkspaceRegistry()
    with pytest.raises(ValueError, match="core legacy message type requires core owner"):
        registry.register_data_source(
            DataSourceDefinition(
                descriptor=DataSourceDescriptor(
                    id="extension.system",
                    kind="snapshot",
                    legacy_message_type="system",
                    default_interval_seconds=1,
                ),
                getter=lambda: {},
            )
        )

    registry.register_owner(RegistryOwner("com.example.extension"))
    with pytest.raises(ValueError, match="must not declare legacy_message_type"):
        registry.register_data_source(
            DataSourceDefinition(
                descriptor=DataSourceDescriptor(
                    id="com.example.extension.source",
                    kind="snapshot",
                    legacy_message_type="custom",
                    default_interval_seconds=1,
                ),
                getter=lambda: {},
            ),
            owner_id="com.example.extension",
        )


def test_owner_contributions_register_atomically_and_keep_owner_metadata():
    registry = WorkspaceRegistry()
    registry.register_data_source(
        DataSourceDefinition(
            descriptor=DataSourceDescriptor(
                id="existing.source",
                kind="snapshot",
                legacy_message_type="existing",
                default_interval_seconds=1,
            ),
            getter=lambda: {},
        )
    )
    contributions = ExtensionContributions(
        data_sources=(
            DataSourceDefinition(
                descriptor=DataSourceDescriptor(
                    id="com.example.atomic.source",
                    kind="snapshot",
                    legacy_message_type=None,
                    default_interval_seconds=1,
                ),
                getter=lambda: {},
            ),
        ),
        widgets=(
            WidgetDefinition(
                type="com.example.atomic.widget",
                title="Atomic",
                sources=("missing.source",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown data source"):
        registry.register_contributions(
            RegistryOwner("com.example.atomic", version="1.0.0"),
            contributions,
        )

    assert "com.example.atomic.source" not in registry.data_source_ids()
    with pytest.raises(KeyError):
        registry.get_owner("com.example.atomic")


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
            revision=1,
            name="Test",
            kind="custom",
            required=False,
            grid=WorkspaceGrid(),
            widgets=(WidgetInstance("test-widget", "test.widget", "main", WidgetLayout(0, 0, 1, 1), WidgetConstraints()),),
        )
    )

    payload = registry.serialize_workspace("test")
    payload["sources"][0]["id"] = "mutated"
    payload["widgets"][0]["sources"].append("mutated")

    fresh = registry.serialize_workspace("test")
    assert fresh["id"] == "test"
    assert fresh["version"] == 3
    assert fresh["revision"] == 1
    assert fresh["name"] == "Test"
    assert fresh["kind"] == "custom"
    assert fresh["required"] is False
    assert fresh["grid"]["columns"] == 16
    assert fresh["grid"]["rows"] == 15
    assert fresh["grid"]["calibration"]["fit_mode"] == "contain"
    assert fresh["sources"][0]["id"] == "test.source"
    assert fresh["widgets"][0]["layout"] == {"x": 0, "y": 0, "width": 1, "height": 1}
    assert fresh["widgets"][0]["constraints"] == {
        "min_width": 1,
        "min_height": 1,
        "max_width": 48,
        "max_height": 48,
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
            version=3,
            revision=1,
            name="Multi",
            kind="custom",
            required=False,
            grid=WorkspaceGrid(),
            widgets=(
                WidgetInstance("first", "test.reusable", "left", WidgetLayout(0, 0, 1, 1), WidgetConstraints()),
                WidgetInstance("second", "test.reusable", "right", WidgetLayout(1, 0, 1, 1), WidgetConstraints()),
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
                version=3,
                revision=1,
                name="Duplicate singleton",
                kind="custom",
                required=False,
                grid=WorkspaceGrid(),
                widgets=(
                    WidgetInstance("first", "test.singleton", "left", WidgetLayout(0, 0, 1, 1), WidgetConstraints()),
                    WidgetInstance("second", "test.singleton", "right", WidgetLayout(1, 0, 1, 1), WidgetConstraints()),
                ),
            )
        )
    with pytest.raises(ValueError, match="duplicate widget id"):
        registry.register_workspace(
            WorkspaceDefinition(
                id="duplicate-id",
                version=3,
                revision=1,
                name="Duplicate id",
                kind="custom",
                required=False,
                grid=WorkspaceGrid(),
                widgets=(
                    WidgetInstance("same", "test.singleton", "left", WidgetLayout(0, 0, 1, 1), WidgetConstraints()),
                    WidgetInstance("same", "test.singleton", "right", WidgetLayout(1, 0, 1, 1), WidgetConstraints()),
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
    assert workspace.sources == ()
    assert {widget.id for widget in workspace.widgets} == {
        "system-info",
        "network",
        "uptime",
        "disks",
        "token-card",
        "player",
        "github",
    }
    assert {widget.type for widget in workspace.widgets} == {
        "builtin.dashboard.system-info",
        "builtin.dashboard.network",
        "builtin.dashboard.uptime",
        "builtin.dashboard.disks",
        "builtin.dashboard.vibe",
        "builtin.dashboard.player",
        "builtin.dashboard.github",
    }
    assert all(registry.get_widget(widget.type).single_instance for widget in workspace.widgets)
    system_types = {
        "builtin.dashboard.system-info",
        "builtin.dashboard.network",
        "builtin.dashboard.uptime",
        "builtin.dashboard.disks",
    }
    system_instances = [widget for widget in workspace.widgets if widget.type in system_types]
    assert len(system_instances) == 4
    assert all(
        registry.get_widget(instance.type).sources == ("system.snapshot",)
        for instance in system_instances
    )
    player_instance = next(
        widget for widget in workspace.widgets if widget.type == "builtin.dashboard.player"
    )
    player = registry.get_widget(player_instance.type)
    assert player.sources == ("media.playback",)
    assert player.channels == ("media.lyric",)
    assert player.single_instance is True

    manifest = registry.serialize_workspace("main")
    assert manifest["id"] == "main"
    assert manifest["version"] == 3
    assert manifest["revision"] == 1
    assert manifest["name"] == "Main Dashboard"
    assert manifest["kind"] == "builtin"
    assert manifest["grid"]["columns"] == 16
    assert manifest["grid"]["rows"] == 15
    assert manifest["grid"]["calibration"]["fit_mode"] == "contain"
    assert manifest["required"] is True
    assert {source["id"] for source in manifest["sources"]} == set(sources)
    assert all("layout" in widget and "constraints" in widget for widget in manifest["widgets"])


def test_workspace_manifest_route_uses_runtime_registry_and_returns_json_404():
    app = create_app({"TESTING": True})
    registry = app.extensions["workspace_registry"]
    client = app.test_client()
    device_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    client.post(
        "/api/device/session",
        json={"device_id": device_id, "page": "dashboard"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    client.patch(
        f"/api/settings/devices/{device_id}",
        json={"status": "approved", "workspace_id": "main"},
        headers={"Origin": "http://localhost"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    headers = {"X-Dashboard-Device": device_id}

    response = client.get("/api/workspaces/main", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == registry.serialize_workspace("main")
    assert response.headers["Cache-Control"] == "no-store"

    missing = client.get("/api/workspaces/unknown", headers=headers)
    assert missing.status_code == 403
    assert missing.headers["Cache-Control"] == "no-store"
    assert missing.get_json()["error"]["code"] == "workspace_not_assigned"
