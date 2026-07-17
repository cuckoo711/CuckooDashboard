"""Isolated Python backend loading for trusted local extensions."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from contracts.extension import (
    ExtensionContributions,
    ExtensionLoadContext,
    ExtensionManifest,
)


class ExtensionLoadError(RuntimeError):
    """A backend entrypoint could not be imported or did not honor the contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoadedExtensionBackend:
    """One imported extension object and its validated contributions."""

    manifest: ExtensionManifest
    instance: Any
    contributions: ExtensionContributions
    namespace: str


def load_extension_backend(
    manifest: ExtensionManifest,
    *,
    namespace: str,
    host_api_version: int = 1,
) -> LoadedExtensionBackend:
    """Import one trusted backend in a manager-specific synthetic package."""
    if not manifest.backend_entrypoint:
        if manifest.contributions.data_sources or manifest.contributions.widgets:
            raise ExtensionLoadError(
                "missing_backend", "extension declares runtime contributions without a backend"
            )
        raise ExtensionLoadError("missing_backend", "extension has no backend entrypoint")

    package_root = Path(manifest.package_root or "").resolve(strict=True)
    module_name, symbol_name = manifest.backend_entrypoint.split(":", 1)
    module_path, is_package = _resolve_module_path(package_root, module_name)
    _ensure_namespace_packages(namespace, package_root, module_name)
    full_module_name = f"{namespace}.{module_name}"
    search_locations = [str(module_path.parent)] if is_package else None
    spec = importlib.util.spec_from_file_location(
        full_module_name,
        module_path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise ExtensionLoadError("import_error", "backend module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_module_name] = module
    try:
        spec.loader.exec_module(module)
        factory = _resolve_symbol(module, symbol_name)
        if not callable(factory):
            raise ExtensionLoadError("contract_error", "backend entrypoint is not callable")
        context = ExtensionLoadContext(
            manifest=manifest,
            package_root=str(package_root),
            source=manifest.source,
            host_api_version=host_api_version,
        )
        instance = factory(context)
        for method_name in ("contributions", "start", "stop"):
            if not callable(getattr(instance, method_name, None)):
                raise ExtensionLoadError(
                    "contract_error", f"extension object is missing {method_name}()"
                )
        contributions = instance.contributions()
        if not isinstance(contributions, ExtensionContributions):
            raise ExtensionLoadError(
                "contract_error", "contributions() must return ExtensionContributions"
            )
    except ExtensionLoadError:
        unload_extension_namespace(namespace)
        raise
    except Exception as exc:
        unload_extension_namespace(namespace)
        raise ExtensionLoadError("import_error", str(exc) or type(exc).__name__) from exc
    return LoadedExtensionBackend(manifest, instance, contributions, namespace)


def unload_extension_namespace(namespace: str) -> None:
    """Remove one synthetic package namespace from ``sys.modules``."""
    prefix = f"{namespace}."
    for module_name in tuple(sys.modules):
        if module_name == namespace or module_name.startswith(prefix):
            sys.modules.pop(module_name, None)


def _resolve_module_path(package_root: Path, module_name: str) -> tuple[Path, bool]:
    parts = module_name.split(".")
    file_candidate = package_root.joinpath(*parts).with_suffix(".py")
    package_candidate = package_root.joinpath(*parts, "__init__.py")
    for candidate, is_package in ((file_candidate, False), (package_candidate, True)):
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(package_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved, is_package
    raise ExtensionLoadError("import_error", f"backend module not found: {module_name}")


def _ensure_namespace_packages(namespace: str, package_root: Path, module_name: str) -> None:
    root = ModuleType(namespace)
    root.__package__ = namespace
    root.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    root.__spec__ = importlib.util.spec_from_loader(namespace, loader=None, is_package=True)
    sys.modules[namespace] = root

    parts = module_name.split(".")[:-1]
    current_name = namespace
    current_path = package_root
    for part in parts:
        current_name = f"{current_name}.{part}"
        current_path = current_path / part
        if current_name in sys.modules:
            continue
        package = ModuleType(current_name)
        package.__package__ = current_name
        package.__path__ = [str(current_path)]  # type: ignore[attr-defined]
        package.__spec__ = importlib.util.spec_from_loader(
            current_name, loader=None, is_package=True
        )
        sys.modules[current_name] = package


def _resolve_symbol(module: ModuleType, dotted_name: str) -> Any:
    value: Any = module
    for part in dotted_name.split("."):
        try:
            value = getattr(value, part)
        except AttributeError as exc:
            raise ExtensionLoadError(
                "contract_error", f"backend symbol not found: {dotted_name}"
            ) from exc
    return value
