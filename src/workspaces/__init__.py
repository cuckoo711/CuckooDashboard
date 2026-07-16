"""Workspace platform registry and built-in definitions."""

from workspaces.builtins import create_builtin_workspace_registry
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import WorkspaceRegistry

__all__ = [
    "DataSourceDefinition",
    "WorkspaceRegistry",
    "create_builtin_workspace_registry",
]
