"""Built-in workspace platform definitions."""

from __future__ import annotations

from contracts.workspace import (
    DataSourceDescriptor,
    DataSourceRefreshPolicy,
    WidgetConstraints,
    WidgetDefinition,
    WidgetInstance,
    WidgetLayout,
    WorkspaceDefinition,
    STANDARD_WORKSPACE_CALIBRATION,
    WorkspaceGrid,
)
from features.dashboard.service import get_dashboard_data
from services.github_service import get_github_data
from services.media_service import get_media_info
from services.system_service import get_system_info
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import CORE_OWNER_ID, RegistryOwner, WorkspaceRegistry


def _source(
    source_id: str,
    getter,
    legacy_message_type: str,
    default_interval_seconds: float,
    active_interval_seconds: float | None = None,
) -> DataSourceDefinition:
    return DataSourceDefinition(
        descriptor=DataSourceDescriptor(
            id=source_id,
            kind="snapshot",
            legacy_message_type=legacy_message_type,
            default_interval_seconds=default_interval_seconds,
            active_interval_seconds=active_interval_seconds,
            refresh_policy=DataSourceRefreshPolicy.from_legacy_intervals(
                default_interval_seconds,
                active_interval_seconds,
            ),
        ),
        getter=getter,
    )


def create_builtin_workspace_registry() -> WorkspaceRegistry:
    """Create a fresh registry containing the required built-in main workspace."""
    registry = WorkspaceRegistry()
    registry.register_owner(
        RegistryOwner(CORE_OWNER_ID, version="1.0.0", locked=True)
    )
    registry.register_data_source(
        _source("system.snapshot", get_system_info, "system", 1),
        owner_id=CORE_OWNER_ID,
    )
    registry.register_data_source(
        _source("media.playback", get_media_info, "media", 1),
        owner_id=CORE_OWNER_ID,
    )
    registry.register_data_source(
        _source("github.contributions", get_github_data, "github", 1),
        owner_id=CORE_OWNER_ID,
    )
    registry.register_data_source(
        _source(
            "dashboard.aggregate",
            get_dashboard_data,
            "dashboard_data",
            60,
            active_interval_seconds=20,
        ),
        owner_id=CORE_OWNER_ID,
    )

    widget_definitions = (
        WidgetDefinition(
            type="builtin.dashboard.system-info",
            title="系统信息",
            sources=("system.snapshot",),
            single_instance=True,
            default_width=6,
            default_height=5,
            constraints=WidgetConstraints(4, 4, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.network",
            title="网络",
            sources=("system.snapshot",),
            single_instance=True,
            default_width=2,
            default_height=3,
            constraints=WidgetConstraints(2, 2, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.uptime",
            title="运行时间",
            sources=("system.snapshot",),
            single_instance=True,
            default_width=2,
            default_height=2,
            constraints=WidgetConstraints(2, 2, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.disks",
            title="磁盘",
            sources=("system.snapshot",),
            single_instance=True,
            default_width=8,
            default_height=4,
            constraints=WidgetConstraints(4, 3, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.player",
            title="播放器",
            sources=("media.playback",),
            channels=("media.lyric",),
            single_instance=True,
            default_width=8,
            default_height=6,
            constraints=WidgetConstraints(6, 4, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.github",
            title="GitHub 贡献",
            sources=("github.contributions",),
            single_instance=True,
            default_width=8,
            default_height=6,
            constraints=WidgetConstraints(6, 4, 48, 48),
        ),
        WidgetDefinition(
            type="builtin.dashboard.vibe",
            title="Vibe Coding",
            sources=("dashboard.aggregate",),
            single_instance=True,
            default_width=8,
            default_height=9,
            constraints=WidgetConstraints(6, 6, 48, 48),
        ),
    )
    for definition in widget_definitions:
        registry.register_widget(definition, owner_id=CORE_OWNER_ID)

    registry.register_workspace(
        WorkspaceDefinition(
            id="main",
            version=3,
            revision=1,
            name="Main Dashboard",
            kind="builtin",
            required=True,
            grid=WorkspaceGrid(columns=16, rows=15, calibration=STANDARD_WORKSPACE_CALIBRATION),
            sources=(),
            widgets=(
                WidgetInstance(
                    "system-info",
                    "builtin.dashboard.system-info",
                    "main",
                    WidgetLayout(0, 0, 6, 5),
                    WidgetConstraints(4, 4, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "network",
                    "builtin.dashboard.network",
                    "main",
                    WidgetLayout(6, 0, 2, 3),
                    WidgetConstraints(2, 2, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "uptime",
                    "builtin.dashboard.uptime",
                    "main",
                    WidgetLayout(6, 3, 2, 2),
                    WidgetConstraints(2, 2, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "disks",
                    "builtin.dashboard.disks",
                    "main",
                    WidgetLayout(0, 5, 8, 4),
                    WidgetConstraints(4, 3, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "token-card",
                    "builtin.dashboard.vibe",
                    "main",
                    WidgetLayout(8, 0, 8, 9),
                    WidgetConstraints(6, 6, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "player",
                    "builtin.dashboard.player",
                    "main",
                    WidgetLayout(0, 9, 8, 6),
                    WidgetConstraints(6, 4, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
                WidgetInstance(
                    "github",
                    "builtin.dashboard.github",
                    "main",
                    WidgetLayout(8, 9, 8, 6),
                    WidgetConstraints(6, 4, 48, 48),
                    owner=CORE_OWNER_ID,
                ),
            ),
        ),
        owner_id=CORE_OWNER_ID,
    )
    return registry
