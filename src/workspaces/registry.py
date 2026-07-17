"""Per-runtime registry for owned data sources, widgets and workspaces."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from contracts.workspace import (
    DataSourceRefreshPolicy,
    WidgetDefinition,
    WidgetInstance,
    WorkspaceDefinition,
    WorkspaceGridCalibration,
)
from workspaces.data_sources import DataSourceDefinition

CORE_OWNER_ID = "cuckoo.core.dashboard"
LEGACY_OWNER_ID = "cuckoo.legacy"
CORE_LEGACY_MESSAGE_TYPES = frozenset({"dashboard_data", "github", "media", "system"})


def _require_identifier(value: str, label: str) -> str:
    identifier = str(value or "")
    if not identifier or identifier.strip() != identifier:
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return identifier


@dataclass(frozen=True)
class RegistryOwner:
    """One package that owns registry contributions."""

    id: str
    version: str = "0.0.0"
    locked: bool = False
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.id, "registry owner id")
        _require_identifier(self.version, "registry owner version")
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        for dependency in self.dependencies:
            _require_identifier(dependency, "registry owner dependency")


class WorkspaceRegistry:
    """Store one application's workspace platform definitions and their owners."""

    def __init__(self) -> None:
        self._data_sources: dict[str, DataSourceDefinition] = {}
        self._widgets: dict[str, WidgetDefinition] = {}
        self._workspaces: dict[str, WorkspaceDefinition] = {}
        self._owners: dict[str, RegistryOwner] = {}
        self._data_source_owners: dict[str, str] = {}
        self._widget_owners: dict[str, str] = {}
        self._workspace_owners: dict[str, str] = {}

    def register_owner(self, owner: RegistryOwner) -> RegistryOwner:
        owner_id = _require_identifier(owner.id, "registry owner id")
        if owner_id in self._owners:
            raise ValueError(f"registry owner already registered: {owner_id}")
        missing = set(owner.dependencies) - self._owners.keys()
        if missing:
            raise ValueError(
                f"registry owner references unavailable dependencies: {', '.join(sorted(missing))}"
            )
        self._owners[owner_id] = owner
        return owner

    def _ensure_owner(self, owner_id: str) -> RegistryOwner:
        owner_id = _require_identifier(owner_id, "registry owner id")
        owner = self._owners.get(owner_id)
        if owner is None:
            owner = RegistryOwner(owner_id)
            self._owners[owner_id] = owner
        return owner

    def register_data_source(
        self,
        definition: DataSourceDefinition,
        *,
        owner_id: str = LEGACY_OWNER_ID,
    ) -> DataSourceDefinition:
        self._ensure_owner(owner_id)
        descriptor = definition.descriptor
        source_id = _require_identifier(descriptor.id, "data source id")
        _require_identifier(descriptor.kind, "data source kind")
        legacy_message_type = descriptor.legacy_message_type
        if legacy_message_type is not None:
            _require_identifier(legacy_message_type, "legacy message type")
            if legacy_message_type in CORE_LEGACY_MESSAGE_TYPES and owner_id != CORE_OWNER_ID:
                raise ValueError(
                    f"core legacy message type requires core owner: {legacy_message_type}"
                )
            if owner_id not in {CORE_OWNER_ID, LEGACY_OWNER_ID}:
                raise ValueError("extension data sources must not declare legacy_message_type")
        self._validate_refresh_policy(descriptor.refresh_policy)
        policy = descriptor.refresh_policy
        if descriptor.default_interval_seconds != policy.default_interval_ms / 1000.0:
            raise ValueError("legacy default interval differs from refresh policy")
        expected_active = (
            None
            if policy.active_interval_ms is None
            else policy.active_interval_ms / 1000.0
        )
        if descriptor.active_interval_seconds != expected_active:
            raise ValueError("legacy active interval differs from refresh policy")
        if source_id in self._data_sources:
            raise ValueError(f"data source already registered: {source_id}")
        self._data_sources[source_id] = definition
        self._data_source_owners[source_id] = owner_id
        return definition

    def register_widget(
        self,
        definition: WidgetDefinition,
        *,
        owner_id: str = LEGACY_OWNER_ID,
    ) -> WidgetDefinition:
        owner = self._ensure_owner(owner_id)
        widget_type = _require_identifier(definition.type, "widget type")
        _require_identifier(definition.title, "widget title")
        unknown_sources = set(definition.sources) - self._data_sources.keys()
        if unknown_sources:
            source_ids = ", ".join(sorted(unknown_sources))
            raise ValueError(f"widget references unknown data source(s): {source_ids}")
        for source_id in definition.sources:
            source_owner = self._data_source_owners[source_id]
            if (
                source_owner != owner_id
                and source_owner != CORE_OWNER_ID
                and source_owner not in owner.dependencies
            ):
                raise ValueError(
                    f"widget references undeclared owner dependency: {source_owner}"
                )
        constraints = definition.constraints
        if min(
            definition.default_width,
            definition.default_height,
            constraints.min_width,
            constraints.min_height,
            constraints.max_width,
            constraints.max_height,
        ) < 1:
            raise ValueError(f"widget has invalid size metadata: {widget_type}")
        if (
            constraints.min_width > constraints.max_width
            or constraints.min_height > constraints.max_height
            or not constraints.min_width <= definition.default_width <= constraints.max_width
            or not constraints.min_height <= definition.default_height <= constraints.max_height
        ):
            raise ValueError(f"widget has inconsistent size metadata: {widget_type}")
        if widget_type in self._widgets:
            raise ValueError(f"widget already registered: {widget_type}")
        self._widgets[widget_type] = definition
        self._widget_owners[widget_type] = owner_id
        return definition

    def register_workspace(
        self,
        definition: WorkspaceDefinition,
        *,
        owner_id: str = LEGACY_OWNER_ID,
    ) -> WorkspaceDefinition:
        self._ensure_owner(owner_id)
        workspace_id = _require_identifier(definition.id, "workspace id")
        if definition.version != 3 or not definition.is_manifest_v3:
            raise ValueError("workspace definitions require Manifest v3")
        _require_identifier(definition.name or "", "workspace name")
        _require_identifier(definition.kind or "", "workspace kind")
        if (definition.revision or 0) < 1:
            raise ValueError("workspace revision must be at least 1")
        if definition.grid is None or not isinstance(definition.grid.calibration, WorkspaceGridCalibration):
            raise ValueError("Manifest v3 workspace metadata is incomplete")
        if workspace_id in self._workspaces:
            raise ValueError(f"workspace already registered: {workspace_id}")

        unknown_sources = set(definition.sources) - self._data_sources.keys()
        instance_ids: set[str] = set()
        singleton_types: set[str] = set()
        for instance in definition.widgets:
            self._validate_widget_instance(instance)
            self._validate_v3_layout(
                instance,
                columns=definition.grid.columns,
                rows=definition.grid.rows,
            )
            if instance.id in instance_ids:
                raise ValueError(f"workspace contains duplicate widget id: {instance.id}")
            instance_ids.add(instance.id)

            widget = self._widgets.get(instance.type)
            if widget is None:
                raise ValueError(f"workspace references unknown widget: {instance.type}")
            if instance.constraints != widget.constraints:
                raise ValueError(f"workspace widget constraints differ from definition: {instance.type}")
            canonical_owner = self._widget_owners[instance.type]
            if instance.owner is not None and instance.owner != canonical_owner:
                raise ValueError(f"workspace widget owner differs from definition: {instance.type}")
            unknown_sources.update(set(widget.sources) - self._data_sources.keys())
            if widget.single_instance:
                if instance.type in singleton_types:
                    raise ValueError(f"workspace repeats single-instance widget: {instance.type}")
                singleton_types.add(instance.type)

        if unknown_sources:
            source_ids = ", ".join(sorted(unknown_sources))
            raise ValueError(f"workspace references unknown data source(s): {source_ids}")
        self._workspaces[workspace_id] = definition
        self._workspace_owners[workspace_id] = owner_id
        return definition

    def register_contributions(self, owner: RegistryOwner, contributions: Any) -> RegistryOwner:
        """Atomically register one owner's declared definitions."""
        snapshots = (
            dict(self._owners),
            dict(self._data_sources),
            dict(self._widgets),
            dict(self._workspaces),
            dict(self._data_source_owners),
            dict(self._widget_owners),
            dict(self._workspace_owners),
        )
        try:
            self.register_owner(owner)
            for definition in tuple(getattr(contributions, "data_sources", ()) or ()):
                self.register_data_source(definition, owner_id=owner.id)
            for definition in tuple(getattr(contributions, "widgets", ()) or ()):
                self.register_widget(definition, owner_id=owner.id)
            for definition in tuple(getattr(contributions, "workspaces", ()) or ()):
                self.register_workspace(definition, owner_id=owner.id)
        except Exception:
            (
                self._owners,
                self._data_sources,
                self._widgets,
                self._workspaces,
                self._data_source_owners,
                self._widget_owners,
                self._workspace_owners,
            ) = snapshots
            raise
        return owner

    @staticmethod
    def _validate_refresh_policy(policy: Any) -> None:
        if not isinstance(policy, DataSourceRefreshPolicy):
            raise ValueError("data source refresh policy is required")
        if type(policy.supports_push) is not bool:
            raise ValueError("refresh policy supports_push must be a boolean")
        if type(policy.pause_without_subscribers) is not bool:
            raise ValueError("refresh policy pause_without_subscribers must be a boolean")
        positive_fields = {
            "default_interval_ms": policy.default_interval_ms,
            "minimum_interval_ms": policy.minimum_interval_ms,
            "error_backoff_initial_ms": policy.error_backoff_initial_ms,
            "error_backoff_max_ms": policy.error_backoff_max_ms,
        }
        for field, value in positive_fields.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"refresh policy {field} must be a positive integer")
        if policy.active_interval_ms is not None and (
            isinstance(policy.active_interval_ms, bool)
            or not isinstance(policy.active_interval_ms, int)
            or policy.active_interval_ms <= 0
        ):
            raise ValueError("refresh policy active_interval_ms must be a positive integer")
        for field, value in (
            ("cache_ttl_ms", policy.cache_ttl_ms),
            ("stale_if_error_ms", policy.stale_if_error_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"refresh policy {field} must be a non-negative integer")
        if policy.minimum_interval_ms > policy.default_interval_ms:
            raise ValueError("refresh policy minimum interval exceeds default interval")
        if (
            policy.active_interval_ms is not None
            and policy.minimum_interval_ms > policy.active_interval_ms
        ):
            raise ValueError("refresh policy minimum interval exceeds active interval")
        if (
            policy.active_interval_ms is not None
            and policy.active_interval_ms > policy.default_interval_ms
        ):
            raise ValueError("refresh policy active interval exceeds default interval")
        if policy.error_backoff_initial_ms > policy.error_backoff_max_ms:
            raise ValueError("refresh policy initial backoff exceeds maximum backoff")

    @staticmethod
    def _validate_widget_instance(instance: WidgetInstance) -> None:
        _require_identifier(instance.id, "widget instance id")
        _require_identifier(instance.type, "widget instance type")
        _require_identifier(instance.slot, "widget instance slot")

    @staticmethod
    def _validate_v3_layout(instance: WidgetInstance, *, columns: int, rows: int) -> None:
        layout = instance.layout
        constraints = instance.constraints
        if layout is None or constraints is None:
            raise ValueError(f"Manifest v3 widget is missing layout or constraints: {instance.id}")
        if layout.x < 0 or layout.y < 0 or layout.width < 1 or layout.height < 1:
            raise ValueError(f"widget has invalid layout: {instance.id}")
        if layout.x + layout.width > columns or layout.y + layout.height > rows:
            raise ValueError(f"widget layout exceeds workspace grid: {instance.id}")
        if min(
            constraints.min_width,
            constraints.min_height,
            constraints.max_width,
            constraints.max_height,
        ) < 1:
            raise ValueError(f"widget has invalid constraints: {instance.id}")
        if constraints.min_width > constraints.max_width or constraints.min_height > constraints.max_height:
            raise ValueError(f"widget has invalid constraints: {instance.id}")
        if not (
            constraints.min_width <= layout.width <= constraints.max_width
            and constraints.min_height <= layout.height <= constraints.max_height
        ):
            raise ValueError(f"widget layout violates constraints: {instance.id}")

    def get_owner(self, owner_id: str) -> RegistryOwner:
        return self._owners[owner_id]

    def iter_owners(self) -> Iterator[RegistryOwner]:
        return iter(tuple(self._owners.values()))

    def owner_of_data_source(self, source_id: str) -> str:
        return self._data_source_owners[source_id]

    def owner_of_widget(self, widget_type: str) -> str:
        return self._widget_owners[widget_type]

    def owner_of_workspace(self, workspace_id: str) -> str:
        return self._workspace_owners[workspace_id]

    def contributions_for_owner(self, owner_id: str) -> dict[str, tuple[str, ...]]:
        return {
            "data_sources": tuple(
                source_id
                for source_id, registered_owner in self._data_source_owners.items()
                if registered_owner == owner_id
            ),
            "widgets": tuple(
                widget_type
                for widget_type, registered_owner in self._widget_owners.items()
                if registered_owner == owner_id
            ),
            "workspaces": tuple(
                workspace_id
                for workspace_id, registered_owner in self._workspace_owners.items()
                if registered_owner == owner_id
            ),
        }

    def get_data_source(self, source_id: str) -> DataSourceDefinition:
        return self._data_sources[source_id]

    def data_source_ids(self) -> tuple[str, ...]:
        return tuple(self._data_sources)

    def iter_data_sources(self) -> Iterator[DataSourceDefinition]:
        return iter(tuple(self._data_sources.values()))

    def get_widget(self, widget_type: str) -> WidgetDefinition:
        return self._widgets[widget_type]

    def iter_widgets(self) -> Iterator[WidgetDefinition]:
        return iter(tuple(self._widgets.values()))

    def get_workspace(self, workspace_id: str) -> WorkspaceDefinition:
        return self._workspaces[workspace_id]

    def serialize_workspace(self, workspace_id: str) -> dict:
        workspace = self.get_workspace(workspace_id)
        source_ids = list(workspace.sources)
        widgets: list[dict[str, Any]] = []
        for instance in workspace.widgets:
            widget = self._widgets[instance.type]
            for source_id in widget.sources:
                if source_id not in source_ids:
                    source_ids.append(source_id)
            canonical = replace(
                instance,
                constraints=widget.constraints,
                owner=self._widget_owners[instance.type],
            )
            widget_payload = canonical.to_payload(widget, manifest_version=3)
            widget_payload["title"] = widget.title
            widgets.append(widget_payload)
        return {
            "id": workspace.id,
            "version": 3,
            "revision": workspace.revision,
            "name": workspace.name,
            "kind": workspace.kind,
            "required": workspace.required,
            "grid": workspace.grid.to_payload(),
            "sources": [
                self._data_sources[source_id].descriptor.to_payload()
                for source_id in source_ids
            ],
            "widgets": widgets,
        }
