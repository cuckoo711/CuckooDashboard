"""Stable JSON-facing contracts for workspace manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DataSourceDescriptor:
    """Public metadata needed to schedule and bridge one data source."""

    id: str
    kind: str
    legacy_message_type: str
    default_interval_seconds: float
    active_interval_seconds: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "legacy_message_type": self.legacy_message_type,
            "default_interval_seconds": self.default_interval_seconds,
            "active_interval_seconds": self.active_interval_seconds,
        }


@dataclass(frozen=True)
class WidgetDefinition:
    """Registered widget type and the runtime capabilities it requires."""

    type: str
    title: str
    sources: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    single_instance: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "channels", tuple(self.channels))


@dataclass(frozen=True)
class WidgetInstance:
    """One placement of a registered widget type inside a workspace."""

    id: str
    type: str
    slot: str

    def to_payload(self, definition: WidgetDefinition) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "slot": self.slot,
            "sources": list(definition.sources),
            "channels": list(definition.channels),
        }


@dataclass(frozen=True)
class WorkspaceDefinition:
    """Immutable workspace composition stored by the registry."""

    id: str
    version: int
    required: bool
    sources: tuple[str, ...] = ()
    widgets: tuple[WidgetInstance, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "widgets", tuple(self.widgets))
