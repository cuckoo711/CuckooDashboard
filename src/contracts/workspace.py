"""Stable JSON-facing contracts for workspace manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
    owner: str | None = None

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
            payload["owner"] = self.owner
            payload["available"] = True
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
