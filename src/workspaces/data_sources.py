"""Data-source definitions owned by a workspace registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from contracts.workspace import DataSourceDescriptor

DataSourceGetter = Callable[[], Any]


@dataclass(frozen=True)
class DataSourceDefinition:
    """Bind immutable public metadata to the callable that fetches its data."""

    descriptor: DataSourceDescriptor
    getter: DataSourceGetter

    def __post_init__(self) -> None:
        if not callable(self.getter):
            raise TypeError("data source getter must be callable")
