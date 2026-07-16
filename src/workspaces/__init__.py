"""Workspace platform registry, persistence and built-in definitions."""

from workspaces.builtins import create_builtin_workspace_registry
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import WorkspaceRegistry
from workspaces.repository import WorkspaceRepository
from workspaces.service import WorkspaceService

__all__ = [
    "DataSourceDefinition",
    "WorkspaceRegistry",
    "WorkspaceRepository",
    "WorkspaceService",
    "create_builtin_workspace_registry",
]
