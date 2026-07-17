"""Manifest v3 dynamic grid and calibration contracts."""

from __future__ import annotations

import pytest

from contracts.workspace import WorkspaceGrid, WorkspaceGridCalibration
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.repository import WorkspaceRepository
from workspaces.service import WorkspaceService, WorkspaceValidationError


def test_grid_accepts_dynamic_bounds_and_rejects_out_of_range_values():
    assert WorkspaceGrid(4, 48).to_payload() == {
        "columns": 4,
        "rows": 48,
        "calibration": {
            "reference_width": 1920,
            "reference_height": 1080,
            "target_cell_width": 120,
            "target_cell_height": 72,
            "fit_mode": "contain",
            "density": "normal",
        },
    }
    with pytest.raises(ValueError):
        WorkspaceGrid(3, 15)
    with pytest.raises(ValueError):
        WorkspaceGrid(16, 49)
    with pytest.raises(ValueError):
        WorkspaceGrid(True, 15)


def test_calibration_is_immutable_and_strictly_serialized():
    calibration = WorkspaceGridCalibration(1920, 1080, 120, 72, "fill")
    assert calibration.to_payload() == {
        "reference_width": 1920,
        "reference_height": 1080,
        "target_cell_width": 120,
        "target_cell_height": 72,
        "fit_mode": "fill",
        "density": "normal",
    }
    with pytest.raises(ValueError):
        WorkspaceGridCalibration(0, 1080, 120, 72, "contain")
    with pytest.raises(ValueError):
        WorkspaceGridCalibration(1920, 1080, 120, 72, "stretch")


def test_service_uses_current_grid_for_bounds_and_rejects_v2_payloads():
    registry = create_builtin_workspace_registry()
    service = WorkspaceService(
        WorkspaceRepository(":memory:"),
        registry,
        seed_workspace=registry.get_workspace("main"),
    )
    created = service.create_blank("Wide", workspace_id="wide")
    manifest = service.serialize(created)
    manifest["grid"] = {"columns": 48, "rows": 48}
    manifest["widgets"] = []
    updated = service.update("wide", manifest)
    assert updated.grid.columns == 48
    assert updated.grid.rows == 48
    assert updated.grid.calibration.density == "normal"

    manifest = service.serialize(updated)
    manifest["version"] = 2
    with pytest.raises(WorkspaceValidationError, match="version must be 3"):
        service.update("wide", manifest)


def test_widget_capability_max_can_exceed_current_grid_but_layout_is_clamped():
    """max_width/max_height are type ceilings (GRID_MAX), not the live grid size."""
    registry = create_builtin_workspace_registry()
    service = WorkspaceService(
        WorkspaceRepository(":memory:"),
        registry,
        seed_workspace=registry.get_workspace("main"),
    )
    blank = service.create_blank("Compact", workspace_id="compact")
    manifest = service.serialize(blank)
    manifest["grid"] = {
        "columns": 12,
        "rows": 12,
        "calibration": manifest["grid"]["calibration"],
    }
    definition = registry.get_widget("builtin.dashboard.network")
    manifest["widgets"] = [
        {
            "id": "network",
            "type": definition.type,
            "owner": "cuckoo.core.dashboard",
            "slot": "main",
            "layout": {"x": 0, "y": 0, "width": 2, "height": 3},
            # Type capability max intentionally exceeds current 12x12 grid.
            "constraints": definition.constraints.to_payload(),
            "sources": list(definition.sources),
            "channels": list(definition.channels),
            "available": True,
        }
    ]
    updated = service.update("compact", manifest)
    assert updated.grid.columns == 12
    widget = updated.widgets[0]
    assert widget.constraints.max_width == 48
    assert widget.layout.width == 2
    assert widget.layout.width <= updated.grid.columns
