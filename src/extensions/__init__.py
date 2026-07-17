"""Extension discovery and desired-state persistence primitives."""

from extensions.discovery import (
    MAX_MANIFEST_BYTES,
    ExtensionDiagnostic,
    ExtensionDiscoveryRecord,
    ExtensionDiscoveryResult,
    compare_semver,
    discover_extensions,
    is_valid_semver,
    parse_semver,
    semver_at_least,
)
from extensions.repository import (
    ExtensionRepository,
    ExtensionRepositoryError,
    ExtensionRevisionConflictError,
    ExtensionStateConflictError,
    ExtensionStateRepository,
    ExtensionStateSnapshot,
)

__all__ = [
    "MAX_MANIFEST_BYTES",
    "ExtensionDiagnostic",
    "ExtensionDiscoveryRecord",
    "ExtensionDiscoveryResult",
    "ExtensionRepository",
    "ExtensionRepositoryError",
    "ExtensionRevisionConflictError",
    "ExtensionStateConflictError",
    "ExtensionStateRepository",
    "ExtensionStateSnapshot",
    "compare_semver",
    "discover_extensions",
    "is_valid_semver",
    "parse_semver",
    "semver_at_least",
]
