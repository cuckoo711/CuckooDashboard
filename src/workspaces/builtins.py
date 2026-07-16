"""Built-in workspace platform definitions."""

from __future__ import annotations

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
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import WorkspaceRegistry


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
        ),
        getter=getter,
    )


def create_builtin_workspace_registry() -> WorkspaceRegistry:
    """Create a fresh registry containing the required built-in main workspace."""
    registry = WorkspaceRegistry()
    registry.register_data_source(_source("system.snapshot", get_system_info, "system", 1))
    registry.register_data_source(_source("media.playback", get_media_info, "media", 1))
    registry.register_data_source(
        _source("github.contributions", get_github_data, "github", 1)
    )
    registry.register_data_source(
        _source(
            "dashboard.aggregate",
            get_dashboard_data,
            "dashboard_data",
            60,
            active_interval_seconds=20,
        )
    )

    widget_definitions = (
        WidgetDefinition(
            type="builtin.system.info",
            title="系统信息",
            sources=("system.snapshot",),
        ),
        WidgetDefinition(
            type="builtin.system.network",
            title="网络",
            sources=("system.snapshot",),
        ),
        WidgetDefinition(
            type="builtin.system.uptime",
            title="运行时间",
            sources=("system.snapshot",),
        ),
        WidgetDefinition(
            type="builtin.system.disks",
            title="磁盘",
            sources=("system.snapshot",),
        ),
        WidgetDefinition(
            type="builtin.media.player",
            title="播放器",
            sources=("media.playback",),
            channels=("media.lyric",),
            single_instance=True,
        ),
        WidgetDefinition(
            type="builtin.github.contributions",
            title="GitHub 贡献",
            sources=("github.contributions",),
        ),
    )
    for definition in widget_definitions:
        registry.register_widget(definition)

    registry.register_workspace(
        WorkspaceDefinition(
            id="main",
            version=1,
            required=True,
            sources=("dashboard.aggregate",),
            widgets=(
                WidgetInstance("system-info", "builtin.system.info", "main"),
                WidgetInstance("system-network", "builtin.system.network", "main"),
                WidgetInstance("system-uptime", "builtin.system.uptime", "main"),
                WidgetInstance("system-disks", "builtin.system.disks", "main"),
                WidgetInstance("media-player", "builtin.media.player", "main"),
                WidgetInstance("github-contributions", "builtin.github.contributions", "main"),
            ),
        )
    )
    return registry
