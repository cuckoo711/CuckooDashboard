"""Backend contributions for the optional Runtime Health extension."""

from __future__ import annotations

from contracts.extension import ExtensionContributions
from contracts.workspace import (
    DataSourceDescriptor,
    DataSourceRefreshPolicy,
    WidgetConstraints,
    WidgetDefinition,
)
from services.health_service import get_health_snapshot
from workspaces.data_sources import DataSourceDefinition

_SOURCE_ID = "com.cuckoo.runtime-health.snapshot"
_WIDGET_TYPE = "com.cuckoo.runtime-health.card"


class RuntimeHealthExtension:
    """Side-effect-free extension object; this sample needs no background worker."""

    def contributions(self) -> ExtensionContributions:
        return ExtensionContributions(
            data_sources=(
                DataSourceDefinition(
                    descriptor=DataSourceDescriptor(
                        id=_SOURCE_ID,
                        kind="snapshot",
                        legacy_message_type=None,
                        default_interval_seconds=5.0,
                        refresh_policy=DataSourceRefreshPolicy.from_legacy_intervals(5.0),
                    ),
                    getter=get_health_snapshot,
                ),
            ),
            widgets=(
                WidgetDefinition(
                    type=_WIDGET_TYPE,
                    title="运行健康",
                    sources=(_SOURCE_ID,),
                    single_instance=True,
                    default_width=4,
                    default_height=3,
                    constraints=WidgetConstraints(4, 3, 48, 48),
                ),
            ),
        )

    def start(self, context) -> None:
        return None

    def stop(self, context, timeout: float) -> None:
        return None


def create_extension(context) -> RuntimeHealthExtension:
    """Create a fresh per-application extension instance."""
    return RuntimeHealthExtension()
