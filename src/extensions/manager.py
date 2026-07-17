"""Discovery, activation state, loading and lifecycle for trusted extensions."""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from contracts.extension import (
    ExtensionContributions,
    ExtensionManifest,
    ExtensionRuntimeContext,
)
from extensions.discovery import (
    ExtensionDiscoveryRecord,
    ExtensionDiscoveryResult,
    discover_extensions,
    semver_at_least,
)
from extensions.loader import (
    ExtensionLoadError,
    LoadedExtensionBackend,
    load_extension_backend,
    unload_extension_namespace,
)
from extensions.repository import (
    ExtensionRevisionConflictError,
    ExtensionStateRepository,
)
from workspaces.registry import CORE_OWNER_ID, RegistryOwner, WorkspaceRegistry


class ExtensionManagerError(RuntimeError):
    """Structured management error returned by Settings routes."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass
class _RuntimeRecord:
    manifest: ExtensionManifest
    desired_at_start: bool
    effective_enabled: bool = False
    backend: LoadedExtensionBackend | None = None
    started: bool = False
    error_code: str | None = None
    error_message: str | None = None


class ExtensionManager:
    """Own one app's discovery snapshot, registry contributions and lifecycle."""

    def __init__(
        self,
        registry: WorkspaceRegistry,
        state_repository: ExtensionStateRepository,
        *,
        builtin_root: str | Path,
        user_root: str | Path,
        workspace_repository: Any = None,
        host_api_version: int = 1,
        mutation_lock: threading.RLock | None = None,
    ) -> None:
        self.registry = registry
        self.state_repository = state_repository
        self.builtin_root = Path(builtin_root)
        self.user_root = Path(user_root)
        self.workspace_repository = workspace_repository
        self.host_api_version = int(host_api_version)
        self._lock = mutation_lock or threading.RLock()
        self._nonce = uuid.uuid4().hex[:12]
        self._discovery = ExtensionDiscoveryResult()
        self._records: dict[str, _RuntimeRecord] = {}
        self._load_order: list[str] = []
        self._desired_states: dict[str, bool] = {}
        self._state_revision = 0
        self._prepared = False

    @property
    def state_revision(self) -> int:
        with self._lock:
            return self._state_revision

    def prepare(self) -> "ExtensionManager":
        """Discover and load the startup effective snapshot exactly once."""
        with self._lock:
            if self._prepared:
                return self
            self._discovery = discover_extensions(self.builtin_root, self.user_root)
            snapshot = self.state_repository.list_snapshot()
            self._state_revision = snapshot.revision
            self._desired_states = dict(snapshot.states)
            manifests = {
                record.manifest.id: record.manifest
                for record in self._discovery.records
                if record.valid and record.manifest is not None
            }
            self._records = {
                extension_id: _RuntimeRecord(
                    manifest=manifest,
                    desired_at_start=bool(self._desired_states.get(extension_id, False)),
                )
                for extension_id, manifest in manifests.items()
            }
            self._load_enabled_records()
            self._prepared = True
            return self

    def rescan(self) -> list[dict[str, Any]]:
        """Refresh descriptor discovery without importing or unloading code."""
        with self._lock:
            self._discovery = discover_extensions(self.builtin_root, self.user_root)
            return self._list_extensions_locked(include_paths=True)

    def list_extensions(self, *, include_paths: bool = True) -> dict[str, Any]:
        with self._lock:
            return {
                "revision": self._state_revision,
                "extensions": self._list_extensions_locked(include_paths=include_paths),
            }

    def set_desired(
        self,
        extension_id: str,
        desired_enabled: bool,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Persist desired state after dependency and workspace-reference checks."""
        extension_id = str(extension_id or "").strip()
        if not extension_id:
            raise ExtensionManagerError("invalid_extension_id", "extension id is required")
        if type(desired_enabled) is not bool:
            raise ExtensionManagerError(
                "invalid_desired_state", "desired_enabled must be a boolean"
            )
        if extension_id == CORE_OWNER_ID:
            raise ExtensionManagerError("extension_locked", "core extension is locked")

        with self._lock:
            if expected_revision != self._state_revision:
                raise ExtensionManagerError(
                    "extension_conflict",
                    "extension state revision conflict",
                    current_revision=self._state_revision,
                )
            valid = self._valid_discovery_records()
            record = valid.get(extension_id)
            current_desired = bool(self._desired_states.get(extension_id, False))
            if desired_enabled == current_desired:
                return self._record_payload(extension_id, include_paths=True)

            if desired_enabled:
                if record is None or record.manifest is None:
                    raise ExtensionManagerError(
                        "extension_not_loadable",
                        "extension is missing, invalid, duplicated, or incompatible",
                    )
                self._validate_enable_requirements(record.manifest, valid)
            else:
                dependents = self._desired_dependents(extension_id, valid)
                if dependents:
                    raise ExtensionManagerError(
                        "extension_has_dependents",
                        "enabled extensions depend on this extension",
                        dependents=dependents,
                    )
                references = self._workspace_references(extension_id)
                if references:
                    raise ExtensionManagerError(
                        "extension_in_use",
                        "workspace widgets still reference this extension",
                        references=references,
                    )
            try:
                snapshot = self.state_repository.set_desired(
                    extension_id,
                    desired_enabled,
                    expected_revision=expected_revision,
                )
            except ExtensionRevisionConflictError as exc:
                self._state_revision = exc.current_revision
                raise ExtensionManagerError(
                    "extension_conflict",
                    "extension state revision conflict",
                    current_revision=exc.current_revision,
                ) from exc
            self._state_revision = snapshot.revision
            self._desired_states = dict(snapshot.states)
            return self._record_payload(extension_id, include_paths=True)

    def start_all(self, runtime: Any = None) -> None:
        """Start loaded extensions in dependency order, isolating failures."""
        with self._lock:
            for extension_id in self._load_order:
                record = self._records.get(extension_id)
                if record is None or not record.effective_enabled or record.backend is None:
                    continue
                if record.started:
                    continue
                failed_dependency = next(
                    (
                        dependency.id
                        for dependency in record.manifest.requires
                        if dependency.id != CORE_OWNER_ID
                        and not self._record_started_or_noop(dependency.id)
                    ),
                    None,
                )
                if failed_dependency:
                    record.error_code = "start_error"
                    record.error_message = f"dependency failed to start: {failed_dependency}"
                    continue
                context = self._runtime_context(record, runtime)
                try:
                    record.backend.instance.start(context)
                    record.started = True
                except Exception as exc:
                    record.error_code = "start_error"
                    record.error_message = str(exc) or type(exc).__name__

    def stop_all(self, runtime: Any = None, *, timeout: float = 5.0) -> None:
        """Stop started extensions in reverse dependency order."""
        timeout = max(0.0, float(timeout))
        with self._lock:
            for extension_id in reversed(self._load_order):
                record = self._records.get(extension_id)
                if record is None or not record.started or record.backend is None:
                    continue
                context = self._runtime_context(record, runtime)
                try:
                    record.backend.instance.stop(context, timeout)
                except Exception as exc:
                    record.error_code = "stop_error"
                    record.error_message = str(exc) or type(exc).__name__
                finally:
                    record.started = False

    def close(self) -> None:
        """Close persistence and remove this manager's synthetic modules."""
        with self._lock:
            self.state_repository.close()
            for record in self._records.values():
                if record.backend is not None:
                    unload_extension_namespace(record.backend.namespace)

    def is_owner_available(self, owner_id: str) -> bool:
        if owner_id == CORE_OWNER_ID:
            return True
        with self._lock:
            record = self._records.get(owner_id)
            return bool(
                record
                and record.effective_enabled
                and record.error_code not in {"start_error"}
            )

    def owner_allows_new_widgets(self, owner_id: str) -> bool:
        if owner_id == CORE_OWNER_ID:
            return True
        with self._lock:
            return self.is_owner_available(owner_id) and bool(
                self._desired_states.get(owner_id, False)
            )

    def owner_unavailable_reason(self, owner_id: str) -> str | None:
        if owner_id == CORE_OWNER_ID:
            return None
        with self._lock:
            record = self._records.get(owner_id)
            if record and record.error_code:
                return record.error_code
            if not self._desired_states.get(owner_id, False):
                return "extension_disabled"
            if record is None:
                return "extension_missing"
            return "extension_unavailable"

    def runtime_catalog(self) -> dict[str, Any]:
        """Return the public, path-free catalog used by the Dashboard loader."""
        with self._lock:
            extensions = [
                {
                    "id": CORE_OWNER_ID,
                    "version": "1.0.0",
                    "locked": True,
                    "requires": [],
                    "module_url": "/static/modules/dashboard/workspace/core-package.js?v=1.0.0",
                    "widget_types": list(
                        self.registry.contributions_for_owner(CORE_OWNER_ID)["widgets"]
                    ),
                }
            ]
            for extension_id in self._load_order:
                record = self._records[extension_id]
                manifest = record.manifest
                if not self.is_owner_available(extension_id) or not manifest.frontend_module:
                    continue
                frontend_relative = PurePosixPath(manifest.frontend_module)
                asset_relative = "/".join(frontend_relative.parts[1:])
                extensions.append(
                    {
                        "id": extension_id,
                        "version": manifest.version,
                        "locked": False,
                        "requires": [item.id for item in manifest.requires],
                        "module_url": (
                            f"/runtime/extensions/{quote(extension_id, safe='')}/assets/"
                            f"{quote(asset_relative, safe='/')}?v={quote(manifest.version, safe='')}"
                        ),
                        "widget_types": list(manifest.contributions.widgets),
                    }
                )
            return {"api_version": self.host_api_version, "extensions": extensions}

    def asset_root(self, extension_id: str) -> Path:
        with self._lock:
            record = self._records.get(extension_id)
            if (
                record is None
                or not self.is_owner_available(extension_id)
                or not record.manifest.frontend_module
            ):
                raise ExtensionManagerError("extension_not_active", "extension is not active")
            root = Path(record.manifest.package_root or "").resolve(strict=True)
            frontend = (root / "frontend").resolve(strict=True)
            frontend.relative_to(root)
            return frontend

    def resolve_asset(self, extension_id: str, filename: str) -> Path:
        root = self.asset_root(extension_id)
        if not filename or "\\" in filename:
            raise ExtensionManagerError("invalid_asset", "invalid extension asset path")
        relative = PurePosixPath(filename)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ExtensionManagerError("invalid_asset", "invalid extension asset path")
        try:
            target = root.joinpath(*relative.parts).resolve(strict=True)
            target.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExtensionManagerError("asset_not_found", "extension asset not found") from exc
        if not target.is_file():
            raise ExtensionManagerError("asset_not_found", "extension asset not found")
        return target

    def health(self) -> dict[str, Any]:
        with self._lock:
            active = [record for record in self._records.values() if record.effective_enabled]
            errors = [record for record in self._records.values() if record.error_code]
            return {
                "discovered": len(self._discovery.records),
                "effective": len(active),
                "started": sum(1 for record in active if record.started),
                "errors": len(errors),
            }

    def _load_enabled_records(self) -> None:
        valid = self._valid_discovery_records()
        candidates = {
            extension_id
            for extension_id, record in self._records.items()
            if record.desired_at_start
        }
        graph: dict[str, set[str]] = {extension_id: set() for extension_id in candidates}
        for extension_id in sorted(candidates):
            record = self._records[extension_id]
            for requirement in record.manifest.requires:
                if requirement.id == CORE_OWNER_ID:
                    if not semver_at_least("1.0.0", requirement.min_version):
                        self._set_error(record, "missing_dependency", requirement.id)
                    continue
                dependency = valid.get(requirement.id)
                dependency_desired = bool(self._desired_states.get(requirement.id, False))
                if (
                    dependency is None
                    or dependency.manifest is None
                    or not dependency_desired
                    or not semver_at_least(
                        dependency.manifest.version, requirement.min_version
                    )
                ):
                    self._set_error(record, "missing_dependency", requirement.id)
                    continue
                graph[extension_id].add(requirement.id)

        order, cycles = self._topological_order(graph)
        for extension_id in cycles:
            self._set_error(self._records[extension_id], "dependency_cycle", extension_id)
        for extension_id in order:
            record = self._records[extension_id]
            if record.error_code:
                continue
            failed_dependency = next(
                (
                    dependency
                    for dependency in graph[extension_id]
                    if not self._records.get(dependency, _RuntimeRecord(record.manifest, False)).effective_enabled
                ),
                None,
            )
            if failed_dependency:
                self._set_error(record, "missing_dependency", failed_dependency)
                continue
            try:
                backend = load_extension_backend(
                    record.manifest,
                    namespace=self._namespace_for(extension_id),
                    host_api_version=self.host_api_version,
                )
                self._validate_contributions(record.manifest, backend.contributions)
                dependencies = tuple(item.id for item in record.manifest.requires)
                self.registry.register_contributions(
                    RegistryOwner(
                        extension_id,
                        version=record.manifest.version,
                        dependencies=dependencies,
                    ),
                    backend.contributions,
                )
                record.backend = backend
                record.effective_enabled = True
                self._load_order.append(extension_id)
            except ExtensionLoadError as exc:
                self._set_error(record, exc.code, str(exc))
            except Exception as exc:
                self._set_error(record, "contribution_conflict", str(exc))

    def _valid_discovery_records(self) -> dict[str, ExtensionDiscoveryRecord]:
        return {
            record.manifest.id: record
            for record in self._discovery.records
            if record.valid and record.manifest is not None
        }

    def _validate_enable_requirements(
        self,
        manifest: ExtensionManifest,
        valid: dict[str, ExtensionDiscoveryRecord],
    ) -> None:
        for requirement in manifest.requires:
            if requirement.id == CORE_OWNER_ID:
                if semver_at_least("1.0.0", requirement.min_version):
                    continue
                raise ExtensionManagerError(
                    "extension_not_loadable", "core dependency version is too old"
                )
            dependency = valid.get(requirement.id)
            if (
                dependency is None
                or dependency.manifest is None
                or not self._desired_states.get(requirement.id, False)
                or not semver_at_least(
                    dependency.manifest.version, requirement.min_version
                )
            ):
                raise ExtensionManagerError(
                    "extension_not_loadable",
                    f"required extension is not enabled: {requirement.id}",
                )

    def _desired_dependents(
        self,
        extension_id: str,
        valid: dict[str, ExtensionDiscoveryRecord],
    ) -> list[str]:
        dependents = []
        for candidate_id, record in valid.items():
            if not self._desired_states.get(candidate_id, False) or record.manifest is None:
                continue
            if any(item.id == extension_id for item in record.manifest.requires):
                dependents.append(candidate_id)
        return sorted(dependents)

    def _workspace_references(self, extension_id: str) -> list[dict[str, object]]:
        if self.workspace_repository is None:
            return []
        getter = getattr(self.workspace_repository, "list_widget_references", None)
        if not callable(getter):
            return []
        return list(getter(extension_id))

    def _list_extensions_locked(self, *, include_paths: bool) -> list[dict[str, Any]]:
        result = [self._core_payload()]
        seen_state_ids: set[str] = set()
        for record in self._discovery.records:
            extension_id = record.id or record.package_root.name
            seen_state_ids.add(extension_id)
            if record.valid and record.manifest is not None:
                result.append(self._record_payload(extension_id, include_paths=include_paths))
                continue
            diagnostics = [
                {"code": item.code, "message": item.message, "field": item.field}
                for item in record.diagnostics
            ]
            payload: dict[str, Any] = {
                "id": extension_id,
                "name": extension_id,
                "version": None,
                "api_version": None,
                "source": record.source,
                "locked": False,
                "desired_enabled": bool(self._desired_states.get(extension_id, False)),
                "effective_enabled": False,
                "restart_required": bool(self._desired_states.get(extension_id, False)),
                "status": "error",
                "error": diagnostics[0] if diagnostics else None,
                "diagnostics": diagnostics,
                "contributions": {"data_sources": [], "widgets": []},
                "requires": [],
                "reference_count": len(self._workspace_references(extension_id)),
            }
            if include_paths:
                payload["package_root"] = str(record.package_root)
            result.append(payload)
        for extension_id in sorted(set(self._desired_states) - seen_state_ids):
            result.append(self._record_payload(extension_id, include_paths=include_paths))
        return result

    def _record_payload(self, extension_id: str, *, include_paths: bool) -> dict[str, Any]:
        discovery = self._valid_discovery_records().get(extension_id)
        manifest = discovery.manifest if discovery is not None else None
        runtime = self._records.get(extension_id)
        desired = bool(self._desired_states.get(extension_id, False))
        effective = bool(runtime and runtime.effective_enabled)
        error_code = runtime.error_code if runtime else None
        error_message = runtime.error_message if runtime else None
        active_version = runtime.manifest.version if runtime and effective else None
        discovered_version = manifest.version if manifest else None
        package_changed = bool(
            effective and discovered_version != active_version
        )
        if manifest is None:
            status = "missing"
            name = extension_id
        elif error_code:
            status = "error"
            name = manifest.metadata.name
        elif desired and not effective:
            status = "pending_enable"
            name = manifest.metadata.name
        elif not desired and effective:
            status = "pending_disable"
            name = manifest.metadata.name
        elif effective:
            status = "active"
            name = manifest.metadata.name
        else:
            status = "disabled"
            name = manifest.metadata.name
        payload: dict[str, Any] = {
            "id": extension_id,
            "name": name,
            "description": manifest.metadata.description if manifest else "",
            "version": discovered_version or active_version,
            "active_version": active_version,
            "api_version": (
                manifest.api_version
                if manifest
                else (runtime.manifest.api_version if runtime else None)
            ),
            "source": (
                manifest.source
                if manifest
                else (runtime.manifest.source if runtime else "missing")
            ),
            "locked": False,
            "desired_enabled": desired,
            "effective_enabled": effective,
            "restart_required": desired != effective or package_changed,
            "status": status,
            "error": (
                {"code": error_code, "message": error_message}
                if error_code
                else None
            ),
            "diagnostics": [],
            "contributions": {
                "data_sources": list(manifest.contributions.data_sources) if manifest else [],
                "widgets": list(manifest.contributions.widgets) if manifest else [],
            },
            "requires": [
                {"id": item.id, "min_version": item.min_version}
                for item in (manifest.requires if manifest else ())
            ],
            "reference_count": len(self._workspace_references(extension_id)),
        }
        if include_paths and manifest and manifest.package_root:
            payload["package_root"] = manifest.package_root
        return payload

    def _core_payload(self) -> dict[str, Any]:
        contributions = self.registry.contributions_for_owner(CORE_OWNER_ID)
        return {
            "id": CORE_OWNER_ID,
            "name": "Cuckoo Core Dashboard",
            "description": "Required built-in dashboard sources and widgets.",
            "version": "1.0.0",
            "api_version": self.host_api_version,
            "source": "core",
            "locked": True,
            "desired_enabled": True,
            "effective_enabled": True,
            "restart_required": False,
            "status": "active",
            "error": None,
            "diagnostics": [],
            "contributions": {
                "data_sources": list(contributions["data_sources"]),
                "widgets": list(contributions["widgets"]),
            },
            "requires": [],
            "reference_count": len(self._workspace_references(CORE_OWNER_ID)),
        }

    def _runtime_context(self, record: _RuntimeRecord, runtime: Any) -> ExtensionRuntimeContext:
        return ExtensionRuntimeContext(
            manifest=record.manifest,
            contributions=(
                record.backend.contributions
                if record.backend is not None
                else ExtensionContributions()
            ),
            services={
                "runtime": runtime,
                "workspace_registry": self.registry,
                "workspace_repository": self.workspace_repository,
            },
        )

    def _record_started_or_noop(self, extension_id: str) -> bool:
        record = self._records.get(extension_id)
        if record is None or not record.effective_enabled:
            return False
        return record.started or record.error_code is None

    def _namespace_for(self, extension_id: str) -> str:
        digest = hashlib.sha256(extension_id.encode("utf-8")).hexdigest()[:12]
        return f"_cuckoo_extension_{self._nonce}_{digest}"

    @staticmethod
    def _validate_contributions(
        manifest: ExtensionManifest,
        contributions: ExtensionContributions,
    ) -> None:
        source_ids = tuple(item.descriptor.id for item in contributions.data_sources)
        widget_types = tuple(item.type for item in contributions.widgets)
        if set(source_ids) != set(manifest.contributions.data_sources) or len(source_ids) != len(
            manifest.contributions.data_sources
        ):
            raise ExtensionLoadError(
                "contract_error", "backend data sources differ from manifest declaration"
            )
        if set(widget_types) != set(manifest.contributions.widgets) or len(widget_types) != len(
            manifest.contributions.widgets
        ):
            raise ExtensionLoadError(
                "contract_error", "backend widgets differ from manifest declaration"
            )
        if widget_types and not manifest.frontend_module:
            raise ExtensionLoadError(
                "contract_error", "widget contributions require a frontend module"
            )

    @staticmethod
    def _topological_order(graph: dict[str, set[str]]) -> tuple[list[str], set[str]]:
        pending = {node: set(dependencies) & graph.keys() for node, dependencies in graph.items()}
        order: list[str] = []
        ready = sorted(node for node, dependencies in pending.items() if not dependencies)
        while ready:
            node = ready.pop(0)
            order.append(node)
            for candidate in sorted(pending):
                if node not in pending[candidate]:
                    continue
                pending[candidate].remove(node)
                if not pending[candidate] and candidate not in order and candidate not in ready:
                    ready.append(candidate)
                    ready.sort()
        return order, set(pending) - set(order)

    @staticmethod
    def _set_error(record: _RuntimeRecord, code: str, message: str) -> None:
        record.error_code = code
        record.error_message = message
