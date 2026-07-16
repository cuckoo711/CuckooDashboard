"""Per-runtime registry for data sources, widgets and workspaces."""

from __future__ import annotations

from collections.abc import Iterator

from contracts.workspace import WidgetDefinition, WidgetInstance, WorkspaceDefinition
from workspaces.data_sources import DataSourceDefinition


def _require_identifier(value: str, label: str) -> str:
    identifier = str(value or "")
    if not identifier or identifier.strip() != identifier:
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return identifier


class WorkspaceRegistry:
    """Store one application's workspace platform definitions."""

    def __init__(self) -> None:
        self._data_sources: dict[str, DataSourceDefinition] = {}
        self._widgets: dict[str, WidgetDefinition] = {}
        self._workspaces: dict[str, WorkspaceDefinition] = {}

    def register_data_source(self, definition: DataSourceDefinition) -> DataSourceDefinition:
        descriptor = definition.descriptor
        source_id = _require_identifier(descriptor.id, "data source id")
        _require_identifier(descriptor.kind, "data source kind")
        _require_identifier(descriptor.legacy_message_type, "legacy message type")
        if descriptor.default_interval_seconds <= 0:
            raise ValueError("default data source interval must be positive")
        if descriptor.active_interval_seconds is not None and descriptor.active_interval_seconds <= 0:
            raise ValueError("active data source interval must be positive")
        if source_id in self._data_sources:
            raise ValueError(f"data source already registered: {source_id}")
        self._data_sources[source_id] = definition
        return definition

    def register_widget(self, definition: WidgetDefinition) -> WidgetDefinition:
        widget_type = _require_identifier(definition.type, "widget type")
        _require_identifier(definition.title, "widget title")
        unknown_sources = set(definition.sources) - self._data_sources.keys()
        if unknown_sources:
            source_ids = ", ".join(sorted(unknown_sources))
            raise ValueError(f"widget references unknown data source(s): {source_ids}")
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
        return definition

    def register_workspace(self, definition: WorkspaceDefinition) -> WorkspaceDefinition:
        workspace_id = _require_identifier(definition.id, "workspace id")
        if definition.version < 1:
            raise ValueError("workspace version must be at least 1")
        if definition.is_manifest_v2:
            _require_identifier(definition.name or "", "workspace name")
            _require_identifier(definition.kind or "", "workspace kind")
            if (definition.revision or 0) < 1:
                raise ValueError("workspace revision must be at least 1")
            if definition.grid is None or definition.grid.columns != 16 or definition.grid.rows != 15:
                raise ValueError("Manifest v2 workspaces must use a 16x15 grid")
        if workspace_id in self._workspaces:
            raise ValueError(f"workspace already registered: {workspace_id}")

        unknown_sources = set(definition.sources) - self._data_sources.keys()
        instance_ids: set[str] = set()
        singleton_types: set[str] = set()
        for instance in definition.widgets:
            self._validate_widget_instance(instance)
            if definition.is_manifest_v2:
                self._validate_v2_layout(instance, columns=16, rows=15)
            if instance.id in instance_ids:
                raise ValueError(f"workspace contains duplicate widget id: {instance.id}")
            instance_ids.add(instance.id)

            widget = self._widgets.get(instance.type)
            if widget is None:
                raise ValueError(f"workspace references unknown widget: {instance.type}")
            if definition.is_manifest_v2 and instance.constraints != widget.constraints:
                raise ValueError(f"workspace widget constraints differ from definition: {instance.type}")
            unknown_sources.update(set(widget.sources) - self._data_sources.keys())
            if widget.single_instance:
                if instance.type in singleton_types:
                    raise ValueError(f"workspace repeats single-instance widget: {instance.type}")
                singleton_types.add(instance.type)

        if unknown_sources:
            source_ids = ", ".join(sorted(unknown_sources))
            raise ValueError(f"workspace references unknown data source(s): {source_ids}")
        self._workspaces[workspace_id] = definition
        return definition

    @staticmethod
    def _validate_widget_instance(instance: WidgetInstance) -> None:
        _require_identifier(instance.id, "widget instance id")
        _require_identifier(instance.type, "widget instance type")
        _require_identifier(instance.slot, "widget instance slot")

    @staticmethod
    def _validate_v2_layout(instance: WidgetInstance, *, columns: int, rows: int) -> None:
        layout = instance.layout
        constraints = instance.constraints
        if layout is None or constraints is None:
            raise ValueError(f"Manifest v2 widget is missing layout or constraints: {instance.id}")
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
        for instance in workspace.widgets:
            widget = self._widgets[instance.type]
            for source_id in widget.sources:
                if source_id not in source_ids:
                    source_ids.append(source_id)
        payload = {
            "id": workspace.id,
            "version": workspace.version,
            "required": workspace.required,
            "sources": [
                self._data_sources[source_id].descriptor.to_payload()
                for source_id in source_ids
            ],
            "widgets": [
                instance.to_payload(
                    self._widgets[instance.type],
                    manifest_version=2 if workspace.is_manifest_v2 else 1,
                )
                for instance in workspace.widgets
            ],
        }
        if workspace.is_manifest_v2:
            payload.update(
                {
                    "revision": workspace.revision,
                    "name": workspace.name,
                    "kind": workspace.kind,
                    "grid": workspace.grid.to_payload(),
                }
            )
        return payload
