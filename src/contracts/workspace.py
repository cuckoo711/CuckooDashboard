"""Stable JSON-facing contracts for workspace manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DataSourceRefreshPolicy:
    """Immutable scheduling, caching and retry policy for one data source."""

    supports_push: bool = False
    default_interval_ms: int = 1000
    active_interval_ms: int | None = None
    minimum_interval_ms: int = 100
    cache_ttl_ms: int = 1000
    pause_without_subscribers: bool = True
    stale_if_error_ms: int = 0
    error_backoff_initial_ms: int = 1000
    error_backoff_max_ms: int = 60000

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DataSourceRefreshPolicy":
        """Parse a JSON-facing policy without silently coercing field types."""
        if not isinstance(payload, Mapping):
            raise TypeError("refresh policy must be an object")
        return cls(
            supports_push=payload.get("supports_push", False),
            default_interval_ms=payload.get("default_interval_ms", 1000),
            active_interval_ms=payload.get("active_interval_ms"),
            minimum_interval_ms=payload.get("minimum_interval_ms", 100),
            cache_ttl_ms=payload.get("cache_ttl_ms", 1000),
            pause_without_subscribers=payload.get("pause_without_subscribers", True),
            stale_if_error_ms=payload.get("stale_if_error_ms", 0),
            error_backoff_initial_ms=payload.get("error_backoff_initial_ms", 1000),
            error_backoff_max_ms=payload.get("error_backoff_max_ms", 60000),
        )

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        return {
            "supports_push": self.supports_push,
            "default_interval_ms": self.default_interval_ms,
            "active_interval_ms": self.active_interval_ms,
            "minimum_interval_ms": self.minimum_interval_ms,
            "cache_ttl_ms": self.cache_ttl_ms,
            "pause_without_subscribers": self.pause_without_subscribers,
            "stale_if_error_ms": self.stale_if_error_ms,
            "error_backoff_initial_ms": self.error_backoff_initial_ms,
            "error_backoff_max_ms": self.error_backoff_max_ms,
        }

    to_dict = to_payload

    @classmethod
    def from_legacy_intervals(
        cls,
        default_interval_seconds: float,
        active_interval_seconds: float | None = None,
    ) -> "DataSourceRefreshPolicy":
        """Build the additive v2 policy for a legacy interval declaration."""
        default_ms = max(1, int(round(float(default_interval_seconds) * 1000)))
        active_ms = (
            None
            if active_interval_seconds is None
            else max(1, int(round(float(active_interval_seconds) * 1000)))
        )
        fastest_ms = min(default_ms, active_ms) if active_ms is not None else default_ms
        return cls(
            default_interval_ms=default_ms,
            active_interval_ms=active_ms,
            minimum_interval_ms=fastest_ms,
            cache_ttl_ms=default_ms,
            error_backoff_initial_ms=default_ms,
            error_backoff_max_ms=max(default_ms, 60000),
        )


@dataclass(frozen=True)
class DataSourceDescriptor:
    """Public metadata needed to schedule and bridge one data source."""

    id: str
    kind: str
    legacy_message_type: str | None
    default_interval_seconds: float
    active_interval_seconds: float | None = None
    refresh_policy: DataSourceRefreshPolicy | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        policy = self.refresh_policy
        if policy is None:
            policy = DataSourceRefreshPolicy.from_legacy_intervals(
                self.default_interval_seconds,
                self.active_interval_seconds,
            )
        else:
            if isinstance(policy, Mapping):
                policy = DataSourceRefreshPolicy.from_payload(policy)
            elif not isinstance(policy, DataSourceRefreshPolicy):
                raise TypeError("refresh_policy must be a DataSourceRefreshPolicy or mapping")
            expected_default = policy.default_interval_ms / 1000.0
            if float(self.default_interval_seconds) != expected_default:
                raise ValueError("legacy default interval differs from refresh policy")
            expected_active = (
                None
                if policy.active_interval_ms is None
                else policy.active_interval_ms / 1000.0
            )
            if self.active_interval_seconds != expected_active:
                raise ValueError("legacy active interval differs from refresh policy")

        object.__setattr__(self, "refresh_policy", policy)
        object.__setattr__(self, "default_interval_seconds", policy.default_interval_ms / 1000.0)
        object.__setattr__(
            self,
            "active_interval_seconds",
            None if policy.active_interval_ms is None else policy.active_interval_ms / 1000.0,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DataSourceDescriptor":
        if not isinstance(payload, Mapping):
            raise TypeError("data source descriptor must be an object")
        policy_payload = payload.get("refresh_policy")
        default_seconds = payload.get("default_interval_seconds")
        active_seconds = payload.get("active_interval_seconds")
        if isinstance(policy_payload, Mapping):
            if default_seconds is None:
                default_seconds = policy_payload.get("default_interval_ms", 1000) / 1000.0
            if active_seconds is None and policy_payload.get("active_interval_ms") is not None:
                active_seconds = policy_payload.get("active_interval_ms") / 1000.0
        return cls(
            id=payload.get("id", ""),
            kind=payload.get("kind", ""),
            legacy_message_type=payload.get("legacy_message_type"),
            default_interval_seconds=default_seconds,
            active_interval_seconds=active_seconds,
            refresh_policy=policy_payload,
        )

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        policy = self.refresh_policy
        assert isinstance(policy, DataSourceRefreshPolicy)
        return {
            "id": self.id,
            "kind": self.kind,
            "legacy_message_type": self.legacy_message_type,
            "default_interval_seconds": self.default_interval_seconds,
            "active_interval_seconds": self.active_interval_seconds,
            "refresh_policy": policy.to_payload(),
        }

    to_dict = to_payload


GRID_MIN = 4
GRID_MAX = 48


@dataclass(frozen=True)
class WorkspaceGrid:
    """Dynamic logical workspace grid dimensions for Manifest v3."""

    columns: int = 16
    rows: int = 15
    calibration: WorkspaceGridCalibration = field(default_factory=lambda: STANDARD_WORKSPACE_CALIBRATION)

    def __post_init__(self) -> None:
        for field_name, value in (("columns", self.columns), ("rows", self.rows)):
            if isinstance(value, bool) or not isinstance(value, int) or not GRID_MIN <= value <= GRID_MAX:
                raise ValueError(f"grid {field_name} must be an integer between {GRID_MIN} and {GRID_MAX}")
        if not isinstance(self.calibration, WorkspaceGridCalibration):
            raise TypeError("grid calibration must be a WorkspaceGridCalibration")

    def to_payload(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "calibration": self.calibration.to_payload(),
        }


@dataclass(frozen=True)
class WorkspaceGridCalibration:
    """Immutable physical sizing hints used to render a logical grid."""

    reference_width: int = 1920
    reference_height: int = 1080
    target_cell_width: int = 120
    target_cell_height: int = 72
    fit_mode: str = "contain"
    density: str = "normal"

    def __post_init__(self) -> None:
        for field, value in (
            ("reference_width", self.reference_width),
            ("reference_height", self.reference_height),
            ("target_cell_width", self.target_cell_width),
            ("target_cell_height", self.target_cell_height),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 16384:
                raise ValueError(f"calibration {field} must be an integer between 1 and 16384")
        if self.target_cell_width > self.reference_width:
            raise ValueError("calibration target_cell_width cannot exceed reference_width")
        if self.target_cell_height > self.reference_height:
            raise ValueError("calibration target_cell_height cannot exceed reference_height")
        if not isinstance(self.fit_mode, str) or self.fit_mode not in {"fill", "contain"}:
            raise ValueError("calibration fit_mode must be fill or contain")
        if not isinstance(self.density, str) or self.density not in {"compact", "normal", "spacious"}:
            raise ValueError("calibration density must be compact, normal or spacious")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkspaceGridCalibration":
        if not isinstance(payload, Mapping):
            raise TypeError("calibration must be an object")
        return cls(
            reference_width=payload.get("reference_width"),
            reference_height=payload.get("reference_height"),
            target_cell_width=payload.get("target_cell_width"),
            target_cell_height=payload.get("target_cell_height"),
            fit_mode=payload.get("fit_mode", "contain"),
            density=payload.get("density", "normal"),
        )

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        return {
            "reference_width": self.reference_width,
            "reference_height": self.reference_height,
            "target_cell_width": self.target_cell_width,
            "target_cell_height": self.target_cell_height,
            "fit_mode": self.fit_mode,
            "density": self.density,
        }

    to_dict = to_payload


STANDARD_WORKSPACE_CALIBRATION = WorkspaceGridCalibration()


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
    max_width: int = GRID_MAX
    max_height: int = GRID_MAX

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
    owner: str | None = None

    def to_payload(self, definition: WidgetDefinition, *, manifest_version: int = 3) -> dict[str, Any]:
        if manifest_version != 3:
            raise ValueError("workspace widget payloads require Manifest v3")
        return {
            "id": self.id,
            "type": self.type,
            "slot": self.slot,
            "sources": list(definition.sources),
            "channels": list(definition.channels),
            "layout": (self.layout or WidgetLayout(0, 0, 1, 1)).to_payload(),
            "constraints": (self.constraints or WidgetConstraints()).to_payload(),
            "owner": self.owner,
            "available": True,
        }


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
        if self.version == 3 and self.grid is None:
            object.__setattr__(self, "grid", WorkspaceGrid())

    @property
    def is_manifest_v3(self) -> bool:
        """Return whether this definition carries complete Manifest v3 metadata."""
        return (
            self.version == 3
            and self.revision is not None
            and self.name is not None
            and self.kind is not None
            and self.grid is not None
            and isinstance(self.grid.calibration, WorkspaceGridCalibration)
        )
