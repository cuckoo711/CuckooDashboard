"""Workspace validation, serialization and persistence orchestration."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from contracts.workspace import (
    WidgetConstraints,
    WidgetInstance,
    WidgetLayout,
    WorkspaceDefinition,
    WorkspaceGrid,
)
from workspaces.registry import WorkspaceRegistry
from workspaces.repository import (
    RequiredWorkspaceError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspaceRepository,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkspaceValidationError(ValueError):
    """A client-supplied manifest is invalid."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field

    def as_dict(self) -> dict[str, str]:
        payload = {"code": "workspace_validation_error", "message": str(self)}
        if self.field:
            payload["field"] = self.field
        return payload


class WorkspaceInUseError(WorkspaceConflictError):
    """An online dashboard still uses the workspace."""


class WorkspaceService:
    """Application service for persistent Manifest v2 workspaces."""

    def __init__(
        self,
        repository: WorkspaceRepository,
        registry: WorkspaceRegistry,
        *,
        seed_workspace: WorkspaceDefinition | None = None,
        is_workspace_in_use: Callable[[str], bool] | None = None,
        is_owner_available: Callable[[str], bool] | None = None,
        owner_allows_new_widgets: Callable[[str], bool] | None = None,
        owner_unavailable_reason: Callable[[str], str | None] | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self._seed_workspace = seed_workspace
        self._is_workspace_in_use = is_workspace_in_use
        self._is_owner_available = is_owner_available
        self._owner_allows_new_widgets = owner_allows_new_widgets
        self._owner_unavailable_reason = owner_unavailable_reason
        self._seed_lock = threading.RLock()
        self._seeded = False

    def _ensure_seeded(self) -> None:
        if self._seeded:
            return
        with self._seed_lock:
            if self._seeded:
                return
            if self._seed_workspace is not None:
                seed = self._canonicalize_workspace(self._seed_workspace)
                self.validate(seed)
                self.repository.seed_workspace(seed)
            self._seeded = True

    def close(self) -> None:
        """Close persistence and allow a later operation to reopen and reseed it."""
        with self._seed_lock:
            self.repository.close()
            self._seeded = False

    def list(self) -> list[dict[str, Any]]:
        return self.list_workspaces()

    def list_workspaces(self) -> list[dict[str, Any]]:
        self._ensure_seeded()
        return [self.serialize_summary(item) for item in self.repository.list_workspaces()]

    def widget_catalog(self) -> list[dict[str, Any]]:
        catalog = []
        for definition in self.registry.iter_widgets():
            owner = self.registry.owner_of_widget(definition.type)
            if not self._owner_is_available(owner) or not self._owner_accepts_new_widgets(owner):
                continue
            catalog.append(
                {
                    "type": definition.type,
                    "title": definition.title,
                    "owner": owner,
                    "available": True,
                    "sources": list(definition.sources),
                    "channels": list(definition.channels),
                    "single_instance": definition.single_instance,
                    "default_size": {
                        "width": definition.default_width,
                        "height": definition.default_height,
                    },
                    "constraints": definition.constraints.to_payload(),
                }
            )
        return catalog

    def get(self, workspace_id: str) -> WorkspaceDefinition:
        self._ensure_seeded()
        return self.repository.get_workspace(workspace_id)

    def get_workspace(self, workspace_id: str) -> WorkspaceDefinition:
        return self.get(workspace_id)

    def create_blank(
        self,
        name: str,
        *,
        workspace_id: str | None = None,
        kind: str = "custom",
    ) -> WorkspaceDefinition:
        self._ensure_seeded()
        workspace = WorkspaceDefinition(
            id=workspace_id or self._new_workspace_id(),
            version=2,
            revision=1,
            name=name,
            kind=kind,
            required=False,
            grid=WorkspaceGrid(),
            widgets=(),
        )
        self.validate(workspace)
        return self.repository.create_workspace(workspace)

    def duplicate(
        self,
        source_id: str,
        *,
        name: str | None = None,
        workspace_id: str | None = None,
    ) -> WorkspaceDefinition:
        source = self.get(source_id)
        copy = replace(
            source,
            id=workspace_id or self._new_workspace_id(),
            name=name or f"{source.name} Copy",
            kind="custom",
            required=False,
            revision=1,
        )
        copy = self._canonicalize_workspace(copy, current=source)
        self.validate(copy, current=source)
        return self.repository.create_workspace(copy)

    def update(
        self,
        workspace_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> WorkspaceDefinition:
        current = self.get(workspace_id)
        revision = expected_revision if expected_revision is not None else payload.get("revision")
        revision = self._integer(revision, "revision", minimum=1)
        workspace = self._definition_from_payload(workspace_id, payload, current=current)
        workspace = self._canonicalize_workspace(workspace, current=current)
        self.validate(workspace, current=current)
        return self.repository.update_workspace(workspace, expected_revision=revision)

    def delete(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
    ) -> WorkspaceDefinition:
        current = self.get(workspace_id)
        if current.required or current.id == "main":
            raise RequiredWorkspaceError("required workspace cannot be deleted")
        if expected_revision is None:
            raise WorkspaceValidationError("revision is required", "revision")
        if self._is_workspace_in_use and self._is_workspace_in_use(workspace_id):
            raise WorkspaceInUseError("workspace is currently in use")
        return self.repository.delete_workspace(
            workspace_id,
            expected_revision=expected_revision,
        )

    def validate(
        self,
        workspace: WorkspaceDefinition,
        *,
        current: WorkspaceDefinition | None = None,
    ) -> WorkspaceDefinition:
        if not isinstance(workspace.id, str) or not _IDENTIFIER.fullmatch(workspace.id):
            raise WorkspaceValidationError("invalid workspace id", "id")
        self._trimmed(workspace.name, "name", maximum=120)
        self._trimmed(workspace.kind, "kind", maximum=64)
        if workspace.version != 2:
            raise WorkspaceValidationError("version must be 2", "version")
        if workspace.revision is None or workspace.revision < 1:
            raise WorkspaceValidationError("revision must be at least 1", "revision")
        grid = workspace.grid
        if grid is None or grid.columns != 16 or grid.rows != 15:
            raise WorkspaceValidationError("grid must be 16 columns by 15 rows", "grid")

        current_widgets = {item.id: item for item in (current.widgets if current else ())}
        widget_ids: set[str] = set()
        singleton_types: set[str] = set()
        occupied: list[tuple[str, WidgetLayout]] = []
        for index, widget in enumerate(workspace.widgets):
            prefix = f"widgets[{index}]"
            if not isinstance(widget.id, str) or not _IDENTIFIER.fullmatch(widget.id):
                raise WorkspaceValidationError("invalid widget id", f"{prefix}.id")
            if widget.id in widget_ids:
                raise WorkspaceValidationError("duplicate widget id", f"{prefix}.id")
            widget_ids.add(widget.id)
            previous = current_widgets.get(widget.id)
            try:
                definition = self.registry.get_widget(widget.type)
            except KeyError:
                definition = None

            if definition is None:
                if (
                    previous is None
                    or previous.type != widget.type
                    or previous.owner != widget.owner
                    or previous.constraints != widget.constraints
                ):
                    raise WorkspaceValidationError(
                        f"unknown widget type: {widget.type}", f"{prefix}.type"
                    )
                constraints = widget.constraints
            else:
                canonical_owner = self.registry.owner_of_widget(widget.type)
                if widget.owner not in {None, canonical_owner}:
                    raise WorkspaceValidationError(
                        "widget owner cannot be changed", f"{prefix}.owner"
                    )
                existing_same_owner = (
                    previous is not None
                    and previous.type == widget.type
                    and previous.owner in {None, canonical_owner}
                )
                if not existing_same_owner and not self._owner_accepts_new_widgets(canonical_owner):
                    raise WorkspaceValidationError(
                        "widget owner is pending disable or unavailable", f"{prefix}.type"
                    )
                if definition.single_instance:
                    if widget.type in singleton_types:
                        raise WorkspaceValidationError(
                            f"single-instance widget repeated: {widget.type}", f"{prefix}.type"
                        )
                    singleton_types.add(widget.type)
                if widget.constraints != definition.constraints:
                    raise WorkspaceValidationError(
                        "widget constraints cannot be changed", f"{prefix}.constraints"
                    )
                constraints = definition.constraints

            self._trimmed(widget.slot, f"{prefix}.slot", maximum=64)
            layout = widget.layout
            if layout is None:
                raise WorkspaceValidationError("layout is required", f"{prefix}.layout")
            if constraints is None:
                raise WorkspaceValidationError("constraints are required", f"{prefix}.constraints")
            for field, value in (
                ("x", layout.x),
                ("y", layout.y),
                ("width", layout.width),
                ("height", layout.height),
            ):
                minimum = 0 if field in {"x", "y"} else 1
                self._integer(value, f"{prefix}.layout.{field}", minimum=minimum)
            if layout.x + layout.width > grid.columns or layout.y + layout.height > grid.rows:
                raise WorkspaceValidationError(
                    "widget layout exceeds workspace grid", f"{prefix}.layout"
                )
            for field, value in (
                ("min_width", constraints.min_width),
                ("min_height", constraints.min_height),
                ("max_width", constraints.max_width),
                ("max_height", constraints.max_height),
            ):
                self._integer(value, f"{prefix}.constraints.{field}", minimum=1)
            if (
                constraints.min_width > constraints.max_width
                or constraints.min_height > constraints.max_height
            ):
                raise WorkspaceValidationError(
                    "minimum constraints cannot exceed maximum constraints",
                    f"{prefix}.constraints",
                )
            if not (
                constraints.min_width <= layout.width <= constraints.max_width
                and constraints.min_height <= layout.height <= constraints.max_height
            ):
                raise WorkspaceValidationError(
                    "widget layout violates constraints", f"{prefix}.layout"
                )
            for other_id, other in occupied:
                if self._overlap(layout, other):
                    raise WorkspaceValidationError(
                        f"widget overlaps {other_id}", f"{prefix}.layout"
                    )
            occupied.append((widget.id, layout))
        return workspace

    def serialize(self, workspace: WorkspaceDefinition | str) -> dict[str, Any]:
        if isinstance(workspace, str):
            workspace = self.get(workspace)
        source_ids: list[str] = []
        for source_id in workspace.sources:
            try:
                owner = self.registry.owner_of_data_source(source_id)
                self.registry.get_data_source(source_id)
            except KeyError:
                continue
            if self._owner_is_available(owner) and source_id not in source_ids:
                source_ids.append(source_id)
        if not source_ids:
            try:
                registered = self.registry.get_workspace(workspace.id)
            except KeyError:
                registered = None
            if registered is not None:
                for source_id in registered.sources:
                    try:
                        owner = self.registry.owner_of_data_source(source_id)
                    except KeyError:
                        continue
                    if self._owner_is_available(owner) and source_id not in source_ids:
                        source_ids.append(source_id)

        widgets: list[dict[str, Any]] = []
        for instance in workspace.widgets:
            try:
                definition = self.registry.get_widget(instance.type)
                owner = self.registry.owner_of_widget(instance.type)
            except KeyError:
                definition = None
                owner = instance.owner or "unknown"
            available = definition is not None and self._owner_is_available(owner)
            if available and definition is not None:
                for source_id in definition.sources:
                    if source_id not in source_ids:
                        source_ids.append(source_id)
                canonical_instance = replace(
                    instance,
                    constraints=definition.constraints,
                    owner=owner,
                )
                payload = canonical_instance.to_payload(definition, manifest_version=2)
                payload["title"] = definition.title
                widgets.append(payload)
                continue

            widgets.append(
                {
                    "id": instance.id,
                    "type": instance.type,
                    "title": instance.type,
                    "slot": instance.slot,
                    "owner": owner,
                    "available": False,
                    "unavailable_reason": self._unavailable_reason(owner),
                    "sources": [],
                    "channels": [],
                    "layout": (instance.layout or WidgetLayout(0, 0, 1, 1)).to_payload(),
                    "constraints": (
                        instance.constraints or WidgetConstraints()
                    ).to_payload(),
                }
            )
        return {
            "id": workspace.id,
            "version": 2,
            "revision": workspace.revision,
            "name": workspace.name,
            "kind": workspace.kind,
            "required": workspace.required,
            "grid": (workspace.grid or WorkspaceGrid()).to_payload(),
            "sources": [
                self.registry.get_data_source(source_id).descriptor.to_payload()
                for source_id in source_ids
            ],
            "widgets": widgets,
        }

    def serialize_summary(self, workspace: WorkspaceDefinition) -> dict[str, Any]:
        unavailable = 0
        for widget in workspace.widgets:
            try:
                owner = self.registry.owner_of_widget(widget.type)
                self.registry.get_widget(widget.type)
            except KeyError:
                unavailable += 1
                continue
            if not self._owner_is_available(owner):
                unavailable += 1
        return {
            "id": workspace.id,
            "version": 2,
            "revision": workspace.revision,
            "name": workspace.name,
            "kind": workspace.kind,
            "required": workspace.required,
            "grid": (workspace.grid or WorkspaceGrid()).to_payload(),
            "widget_count": len(workspace.widgets),
            "unavailable_widget_count": unavailable,
        }

    def _definition_from_payload(
        self,
        workspace_id: str,
        payload: Mapping[str, Any],
        *,
        current: WorkspaceDefinition,
    ) -> WorkspaceDefinition:
        supplied_id = payload.get("id", workspace_id)
        if supplied_id != workspace_id:
            raise WorkspaceValidationError("workspace id cannot be changed", "id")
        version = payload.get("version", 2)
        if version != 2:
            raise WorkspaceValidationError("version must be 2", "version")
        if "kind" in payload and payload.get("kind") != current.kind:
            raise WorkspaceValidationError("workspace kind cannot be changed", "kind")
        if "required" in payload and bool(payload.get("required")) != current.required:
            raise WorkspaceValidationError("workspace required flag cannot be changed", "required")
        grid_payload = payload.get("grid")
        grid = current.grid or WorkspaceGrid()
        if grid_payload is not None:
            if not isinstance(grid_payload, Mapping):
                raise WorkspaceValidationError("grid must be an object", "grid")
            grid = WorkspaceGrid(
                self._integer(grid_payload.get("columns"), "grid.columns", minimum=1),
                self._integer(grid_payload.get("rows"), "grid.rows", minimum=1),
            )
        widgets_payload = payload.get("widgets")
        widgets = current.widgets
        if widgets_payload is not None:
            if not isinstance(widgets_payload, list):
                raise WorkspaceValidationError("widgets must be an array", "widgets")
            current_widgets = {widget.id: widget for widget in current.widgets}
            widgets = tuple(
                self._widget_from_payload(
                    widget,
                    index,
                    current_widget=current_widgets.get(
                        str(widget.get("id") or "") if isinstance(widget, Mapping) else ""
                    ),
                )
                for index, widget in enumerate(widgets_payload)
            )
        return WorkspaceDefinition(
            id=workspace_id,
            version=2,
            revision=current.revision,
            name=payload.get("name", current.name),
            kind=current.kind,
            required=current.required,
            grid=grid,
            widgets=widgets,
        )

    def _widget_from_payload(
        self,
        payload: Any,
        index: int,
        *,
        current_widget: WidgetInstance | None = None,
    ) -> WidgetInstance:
        prefix = f"widgets[{index}]"
        if not isinstance(payload, Mapping):
            raise WorkspaceValidationError("widget must be an object", prefix)
        layout = payload.get("layout")
        constraints = payload.get("constraints")
        if not isinstance(layout, Mapping):
            raise WorkspaceValidationError("layout must be an object", f"{prefix}.layout")
        if not isinstance(constraints, Mapping):
            raise WorkspaceValidationError(
                "constraints must be an object", f"{prefix}.constraints"
            )
        widget_id = str(payload.get("id") or "")
        widget_type = str(payload.get("type") or "")
        supplied_constraints = WidgetConstraints(
            self._integer(
                constraints.get("min_width"),
                f"{prefix}.constraints.min_width",
                minimum=1,
            ),
            self._integer(
                constraints.get("min_height"),
                f"{prefix}.constraints.min_height",
                minimum=1,
            ),
            self._integer(
                constraints.get("max_width"),
                f"{prefix}.constraints.max_width",
                minimum=1,
            ),
            self._integer(
                constraints.get("max_height"),
                f"{prefix}.constraints.max_height",
                minimum=1,
            ),
        )
        try:
            definition = self.registry.get_widget(widget_type)
        except KeyError:
            definition = None

        if definition is None:
            if current_widget is None or current_widget.type != widget_type:
                raise WorkspaceValidationError(
                    f"unknown widget type: {widget_type}", f"{prefix}.type"
                )
            owner = str(payload.get("owner") or current_widget.owner or "")
            if owner != (current_widget.owner or ""):
                raise WorkspaceValidationError(
                    "widget owner cannot be changed", f"{prefix}.owner"
                )
            if supplied_constraints != current_widget.constraints:
                raise WorkspaceValidationError(
                    "widget constraints cannot be changed", f"{prefix}.constraints"
                )
            canonical_constraints = supplied_constraints
        else:
            owner = self.registry.owner_of_widget(widget_type)
            supplied_owner = payload.get("owner")
            if supplied_owner is not None and str(supplied_owner) != owner:
                raise WorkspaceValidationError(
                    "widget owner cannot be changed", f"{prefix}.owner"
                )
            if supplied_constraints != definition.constraints:
                raise WorkspaceValidationError(
                    "widget constraints cannot be changed", f"{prefix}.constraints"
                )
            canonical_constraints = definition.constraints

        return WidgetInstance(
            id=widget_id,
            type=widget_type,
            slot=str(payload.get("slot") or "main"),
            layout=WidgetLayout(
                self._integer(layout.get("x"), f"{prefix}.layout.x", minimum=0),
                self._integer(layout.get("y"), f"{prefix}.layout.y", minimum=0),
                self._integer(layout.get("width"), f"{prefix}.layout.width", minimum=1),
                self._integer(layout.get("height"), f"{prefix}.layout.height", minimum=1),
            ),
            constraints=canonical_constraints,
            owner=owner,
        )

    def list_owner_references(self, owner_id: str) -> list[dict[str, object]]:
        self._ensure_seeded()
        return self.repository.list_widget_references(owner_id)

    def _canonicalize_workspace(
        self,
        workspace: WorkspaceDefinition,
        *,
        current: WorkspaceDefinition | None = None,
    ) -> WorkspaceDefinition:
        canonical: list[WidgetInstance] = []
        for instance in workspace.widgets:
            try:
                definition = self.registry.get_widget(instance.type)
                owner = self.registry.owner_of_widget(instance.type)
            except KeyError:
                canonical.append(instance)
                continue
            canonical.append(
                replace(
                    instance,
                    constraints=definition.constraints,
                    owner=owner,
                )
            )
        return replace(workspace, widgets=tuple(canonical))

    def _owner_is_available(self, owner_id: str) -> bool:
        if self._is_owner_available is not None:
            try:
                return bool(self._is_owner_available(owner_id))
            except Exception:
                return False
        try:
            self.registry.get_owner(owner_id)
            return True
        except KeyError:
            return False

    def _owner_accepts_new_widgets(self, owner_id: str) -> bool:
        if not self._owner_is_available(owner_id):
            return False
        if self._owner_allows_new_widgets is None:
            return True
        try:
            return bool(self._owner_allows_new_widgets(owner_id))
        except Exception:
            return False

    def _unavailable_reason(self, owner_id: str) -> str:
        if self._owner_unavailable_reason is not None:
            try:
                reason = self._owner_unavailable_reason(owner_id)
            except Exception:
                reason = None
            if reason:
                return str(reason)
        return "extension_unavailable"

    @staticmethod
    def _integer(value: Any, field: str, *, minimum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise WorkspaceValidationError(
                f"must be an integer greater than or equal to {minimum}", field
            )
        return value

    @staticmethod
    def _trimmed(value: Any, field: str, *, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise WorkspaceValidationError("must be a non-empty trimmed string", field)
        if len(value) > maximum:
            raise WorkspaceValidationError(f"must be at most {maximum} characters", field)
        return value

    @staticmethod
    def _overlap(first: WidgetLayout, second: WidgetLayout) -> bool:
        return not (
            first.x + first.width <= second.x
            or second.x + second.width <= first.x
            or first.y + first.height <= second.y
            or second.y + second.height <= first.y
        )

    @staticmethod
    def _new_workspace_id() -> str:
        return f"workspace-{uuid.uuid4().hex[:12]}"


__all__ = [
    "RequiredWorkspaceError",
    "WorkspaceConflictError",
    "WorkspaceInUseError",
    "WorkspaceNotFoundError",
    "WorkspaceService",
    "WorkspaceValidationError",
]
