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
class WorkspaceGrid:
    """Fixed dashboard grid dimensions used by Manifest v2."""

    columns: int = 16
    rows: int = 15

    def to_payload(self) -> dict[str, int]:
        return {"columns": self.columns, "rows": self.rows}


@dataclass(frozen=True)
class WidgetLayout:
    """Zero-based widget placement inside a workspace grid."""

    x: int
    y: int
    width: int
    height: int

    def to_payload(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class WidgetConstraints:
    """Resize limits for one widget instance."""

    min_width: int = 1
    min_height: int = 1
    max_width: int = 16
    max_height: int = 15

    def to_payload(self) -> dict[str, int]:
        return {
            "min_width": self.min_width,
            "min_height": self.min_height,
            "max_width": self.max_width,
            "max_height": self.max_height,
        }


@dataclass(frozen=True)
class WidgetDefinition:
    """Registered widget type and the runtime capabilities it requires."""

    type: str
    title: str
    sources: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    single_instance: bool = False
    default_width: int = 1
    default_height: int = 1
    constraints: WidgetConstraints = WidgetConstraints()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "channels", tuple(self.channels))


@dataclass(frozen=True)
class WidgetInstance:
    """One placement of a registered widget type inside a workspace."""

    id: str
    type: str
    slot: str
    layout: WidgetLayout | None = None
    constraints: WidgetConstraints | None = None

    def to_payload(self, definition: WidgetDefinition, *, manifest_version: int = 1) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "slot": self.slot,
            "sources": list(definition.sources),
            "channels": list(definition.channels),
        }
        if manifest_version >= 2:
            payload["layout"] = (self.layout or WidgetLayout(0, 0, 1, 1)).to_payload()
            payload["constraints"] = (self.constraints or WidgetConstraints()).to_payload()
        return payload


@dataclass(frozen=True)
class WorkspaceDefinition:
    """Immutable workspace composition stored by the registry or repository."""

    id: str
    version: int
    required: bool
    sources: tuple[str, ...] = ()
    widgets: tuple[WidgetInstance, ...] = ()
    revision: int | None = None
    name: str | None = None
    kind: str | None = None
    grid: WorkspaceGrid | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "widgets", tuple(self.widgets))

    @property
    def is_manifest_v2(self) -> bool:
        """Return whether this definition carries the v2 persistence metadata."""
        return (
            self.version >= 2
            and self.revision is not None
            and self.name is not None
            and self.kind is not None
            and self.grid is not None
        )
