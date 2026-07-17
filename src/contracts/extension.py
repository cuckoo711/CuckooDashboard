"""Standard-library-only contracts for host-managed extensions.

The types in this module describe data crossing the extension boundary.  They do
not perform discovery, imports, persistence, or framework integration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtensionMetadata:
    """Human-facing metadata declared by an extension manifest."""

    name: str
    description: str = ""
    author: str | None = None
    homepage: str | None = None
    license: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtensionRequirement:
    """A dependency on another extension at a minimum semantic version."""

    id: str
    min_version: str = "0.0.0"


@dataclass(frozen=True)
class ExtensionContributionDeclaration:
    """Identifiers an extension promises to contribute after it is loaded."""

    data_sources: tuple[str, ...] = ()
    widgets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_sources", tuple(self.data_sources))
        object.__setattr__(self, "widgets", tuple(self.widgets))


@dataclass(frozen=True)
class ExtensionManifest:
    """Validated manifest data plus host-owned discovery information."""

    manifest_version: int
    id: str
    version: str
    api_version: int
    metadata: ExtensionMetadata
    backend_entrypoint: str | None = None
    frontend_module: str | None = None
    contributions: ExtensionContributionDeclaration = ExtensionContributionDeclaration()
    requires: tuple[ExtensionRequirement, ...] = ()
    source: str | None = None
    package_root: str | None = None
    manifest_path: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))


@dataclass(frozen=True)
class ExtensionContributions:
    """Concrete contributions returned by a loaded backend extension."""

    data_sources: tuple[Any, ...] = ()
    widgets: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_sources", tuple(self.data_sources))
        object.__setattr__(self, "widgets", tuple(self.widgets))


@dataclass(frozen=True)
class ExtensionLoadContext:
    """Host services and immutable metadata available during backend loading."""

    manifest: ExtensionManifest
    package_root: str
    source: str | None = None
    host_api_version: int | None = None
    services: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtensionRuntimeContext:
    """Runtime state supplied when an already loaded extension is started."""

    manifest: ExtensionManifest
    contributions: ExtensionContributions = ExtensionContributions()
    services: Mapping[str, Any] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExtensionContract(Protocol):
    """Lifecycle implemented by an extension backend entrypoint."""

    def contributions(self) -> ExtensionContributions:
        """Return the extension's concrete, side-effect-free contributions."""
        ...

    def start(self, context: ExtensionRuntimeContext) -> None:
        """Start runtime work after all enabled extensions have been loaded."""
        ...

    def stop(self, context: ExtensionRuntimeContext, timeout: float) -> None:
        """Release runtime resources; implementations should be idempotent."""
        ...
