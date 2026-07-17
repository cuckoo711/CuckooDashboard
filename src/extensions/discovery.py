"""Deterministic, import-free discovery of extension manifests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from contracts.extension import (
    ExtensionContributionDeclaration,
    ExtensionManifest,
    ExtensionMetadata,
    ExtensionRequirement,
)

MAX_MANIFEST_BYTES = 128 * 1024

_EXTENSION_ID_RE = re.compile(
    r"^[a-z](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z](?:[a-z0-9-]*[a-z0-9])?)+$"
)
_CONTRIBUTION_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_BACKEND_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")

_TOP_LEVEL_FIELDS = {
    "manifest_version",
    "id",
    "version",
    "api_version",
    "metadata",
    "backend",
    "frontend",
    "contributions",
    "requires",
}
_METADATA_FIELDS = {"name", "description", "author", "homepage", "license"}
_BACKEND_FIELDS = {"entrypoint"}
_FRONTEND_FIELDS = {"module"}
_CONTRIBUTION_FIELDS = {"data_sources", "widgets"}
_REQUIREMENT_FIELDS = {"id", "min_version"}


@dataclass(frozen=True)
class ExtensionDiagnostic:
    """A stable machine-readable discovery or validation problem."""

    code: str
    message: str
    source: str | None = None
    package_root: Path | None = None
    manifest_path: Path | None = None
    extension_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class ExtensionDiscoveryRecord:
    """Discovery outcome for one direct child containing ``extension.json``."""

    source: str
    package_root: Path
    manifest_path: Path
    extension_id: str | None = None
    manifest: ExtensionManifest | None = None
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()

    @property
    def id(self) -> str | None:
        """Return the declared id, including for otherwise invalid manifests."""

        return self.extension_id

    @property
    def valid(self) -> bool:
        return self.manifest is not None and not self.diagnostics


@dataclass(frozen=True)
class ExtensionDiscoveryResult:
    """Stable snapshot produced by scanning all configured extension roots."""

    records: tuple[ExtensionDiscoveryRecord, ...] = ()
    root_diagnostics: tuple[ExtensionDiagnostic, ...] = ()

    @property
    def diagnostics(self) -> tuple[ExtensionDiagnostic, ...]:
        return self.root_diagnostics + tuple(
            diagnostic for record in self.records for diagnostic in record.diagnostics
        )

    @property
    def manifests(self) -> tuple[ExtensionManifest, ...]:
        return tuple(record.manifest for record in self.records if record.valid and record.manifest)


@dataclass(frozen=True)
class _ParsedSemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


def parse_semver(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    """Parse a strict SemVer 2.0 string, raising ``ValueError`` when invalid."""

    if not isinstance(value, str):
        raise ValueError("semantic version must be a string")
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid semantic version: {value!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
        raise ValueError(f"invalid semantic version: {value!r}")
    parsed = _ParsedSemVer(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)
    return parsed.major, parsed.minor, parsed.patch, parsed.prerelease


def is_valid_semver(value: object) -> bool:
    """Return whether *value* is a strict SemVer 2.0 string."""

    try:
        parse_semver(value)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def compare_semver(left: str, right: str) -> int:
    """Compare SemVer precedence, ignoring build metadata as required by SemVer."""

    left_version = parse_semver(left)
    right_version = parse_semver(right)
    left_core = left_version[:3]
    right_core = right_version[:3]
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    return _compare_prerelease(left_version[3], right_version[3])


def semver_at_least(version: str, minimum: str) -> bool:
    """Return whether *version* has precedence greater than or equal to *minimum*."""

    return compare_semver(version, minimum) >= 0


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_part) < int(right_part) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_part < right_part else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def discover_extensions(
    builtin_root: str | Path,
    user_root: str | Path,
    *,
    source_names: tuple[str, str] = ("builtin", "user"),
    max_manifest_bytes: int = MAX_MANIFEST_BYTES,
) -> ExtensionDiscoveryResult:
    """Scan the direct child directories of two roots without importing backends.

    Missing roots are treated as empty and are never created.  Records are ordered
    by root order and then by case-sensitive directory name.
    """

    if len(source_names) != 2:
        raise ValueError("source_names must contain exactly two names")
    if max_manifest_bytes <= 0:
        raise ValueError("max_manifest_bytes must be positive")

    records: list[ExtensionDiscoveryRecord] = []
    root_diagnostics: list[ExtensionDiagnostic] = []
    for root_value, source in zip((builtin_root, user_root), source_names):
        root_records, diagnostics = _scan_root(
            Path(root_value), source, max_manifest_bytes=max_manifest_bytes
        )
        records.extend(root_records)
        root_diagnostics.extend(diagnostics)

    records = _mark_duplicate_ids(records)
    return ExtensionDiscoveryResult(tuple(records), tuple(root_diagnostics))


def _scan_root(
    root: Path,
    source: str,
    *,
    max_manifest_bytes: int,
) -> tuple[list[ExtensionDiscoveryRecord], list[ExtensionDiagnostic]]:
    if not root.exists():
        return [], []
    if not root.is_dir():
        return [], [
            ExtensionDiagnostic(
                "root_not_directory",
                "extension root is not a directory",
                source=source,
                package_root=root,
            )
        ]
    try:
        root_resolved = root.resolve(strict=True)
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except (OSError, RuntimeError):
        return [], [
            ExtensionDiagnostic(
                "root_unreadable",
                "extension root cannot be read",
                source=source,
                package_root=root,
            )
        ]

    records: list[ExtensionDiscoveryRecord] = []
    for child in children:
        try:
            is_directory = child.is_dir()
        except (OSError, RuntimeError):
            continue
        if not is_directory:
            continue
        manifest_path = child / "extension.json"
        try:
            has_manifest = manifest_path.is_file()
        except (OSError, RuntimeError):
            has_manifest = False
        if not has_manifest:
            continue
        records.append(
            _read_candidate(
                child,
                manifest_path,
                root_resolved,
                source,
                max_manifest_bytes=max_manifest_bytes,
            )
        )
    return records, []


def _read_candidate(
    package_root: Path,
    manifest_path: Path,
    scan_root: Path,
    source: str,
    *,
    max_manifest_bytes: int,
) -> ExtensionDiscoveryRecord:
    base = {
        "source": source,
        "package_root": package_root,
        "manifest_path": manifest_path,
    }
    try:
        resolved_package = package_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("package_unreadable", "extension package cannot be resolved", **base),),
        )
    if not _is_within(resolved_package, scan_root):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("package_escape", "extension package escapes its scan root", **base),),
        )
    try:
        resolved_manifest = manifest_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("manifest_unreadable", "extension manifest cannot be resolved", **base),),
        )
    if not _is_within(resolved_manifest, resolved_package):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("manifest_escape", "extension manifest escapes its package", **base),),
        )

    try:
        with resolved_manifest.open("rb") as handle:
            payload = handle.read(max_manifest_bytes + 1)
    except (OSError, RuntimeError):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("manifest_read_error", "extension manifest cannot be read", **base),),
        )
    if len(payload) > max_manifest_bytes:
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(
                _diagnostic(
                    "manifest_too_large",
                    f"extension manifest exceeds {max_manifest_bytes} bytes",
                    **base,
                ),
            ),
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("invalid_utf8", "extension manifest is not valid UTF-8", **base),),
        )
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("invalid_json", "extension manifest is not valid JSON", **base),),
        )
    if not isinstance(document, dict):
        return ExtensionDiscoveryRecord(
            **base,
            diagnostics=(_diagnostic("manifest_not_object", "extension manifest must be an object", **base),),
        )

    manifest, extension_id, diagnostics = _validate_document(
        document,
        package_root=resolved_package,
        manifest_path=resolved_manifest,
        directory_name=package_root.name,
        source=source,
    )
    bound = tuple(
        replace(
            diagnostic,
            source=source,
            package_root=package_root,
            manifest_path=manifest_path,
            extension_id=diagnostic.extension_id or extension_id,
        )
        for diagnostic in diagnostics
    )
    return ExtensionDiscoveryRecord(
        **base,
        extension_id=extension_id,
        manifest=manifest,
        diagnostics=bound,
    )


def _validate_document(
    document: Mapping[str, Any],
    *,
    package_root: Path,
    manifest_path: Path,
    directory_name: str,
    source: str,
) -> tuple[ExtensionManifest | None, str | None, list[ExtensionDiagnostic]]:
    diagnostics: list[ExtensionDiagnostic] = []
    _reject_unknown_fields(document, _TOP_LEVEL_FIELDS, "", diagnostics)
    for field_name in ("manifest_version", "id", "version", "api_version", "metadata", "contributions"):
        if field_name not in document:
            diagnostics.append(_field_diagnostic("missing_field", field_name, "required field is missing"))

    manifest_version = document.get("manifest_version")
    if type(manifest_version) is not int or manifest_version != 1:
        diagnostics.append(
            _field_diagnostic("invalid_manifest_version", "manifest_version", "manifest_version must be 1")
        )

    raw_id = document.get("id")
    extension_id = raw_id if isinstance(raw_id, str) else None
    if extension_id is None or _EXTENSION_ID_RE.fullmatch(extension_id) is None:
        diagnostics.append(
            _field_diagnostic("invalid_id", "id", "id must be a lowercase reverse-domain identifier")
        )
        extension_id = None
    elif extension_id != directory_name:
        diagnostics.append(
            _field_diagnostic("directory_id_mismatch", "id", "id must exactly match the package directory name")
        )

    version = document.get("version")
    if not is_valid_semver(version):
        diagnostics.append(
            _field_diagnostic("invalid_semver", "version", "version must be a valid semantic version")
        )
    api_version = document.get("api_version")
    if type(api_version) is not int or api_version != 1:
        diagnostics.append(
            _field_diagnostic("incompatible_api", "api_version", "api_version must be 1")
        )

    metadata = _validate_metadata(document.get("metadata"), diagnostics)
    declarations = _validate_contributions(document.get("contributions"), diagnostics)
    requirements = _validate_requirements(document.get("requires", []), extension_id, diagnostics)
    backend = _validate_backend(document.get("backend"), package_root, diagnostics)
    frontend = _validate_frontend(document.get("frontend"), package_root, diagnostics)
    if extension_id and declarations:
        for field_name, identifiers in (
            ("data_sources", declarations.data_sources),
            ("widgets", declarations.widgets),
        ):
            for identifier in identifiers:
                if not identifier.startswith(f"{extension_id}."):
                    diagnostics.append(
                        _field_diagnostic(
                            "invalid_contribution_prefix",
                            f"contributions.{field_name}",
                            f"contribution id must start with {extension_id}.",
                        )
                    )

    if diagnostics:
        return None, extension_id, diagnostics

    assert extension_id is not None
    assert metadata is not None
    assert declarations is not None
    top_extensions = {key: value for key, value in document.items() if key.startswith("x-")}
    manifest = ExtensionManifest(
        manifest_version=1,
        id=extension_id,
        version=document["version"],
        api_version=document["api_version"],
        metadata=metadata,
        backend_entrypoint=backend,
        frontend_module=frontend,
        contributions=declarations,
        requires=tuple(requirements),
        source=source,
        package_root=str(package_root),
        manifest_path=str(manifest_path),
        extensions=top_extensions,
    )
    return manifest, extension_id, diagnostics


def _validate_metadata(
    value: object, diagnostics: list[ExtensionDiagnostic]
) -> ExtensionMetadata | None:
    if not isinstance(value, dict):
        diagnostics.append(_field_diagnostic("invalid_metadata", "metadata", "metadata must be an object"))
        return None
    _reject_unknown_fields(value, _METADATA_FIELDS, "metadata", diagnostics)
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        diagnostics.append(_field_diagnostic("invalid_metadata", "metadata.name", "metadata.name must be non-empty"))
    description = value.get("description", "")
    if not isinstance(description, str):
        diagnostics.append(_field_diagnostic("invalid_metadata", "metadata.description", "metadata.description must be a string"))
    optional: dict[str, str | None] = {}
    for key in ("author", "homepage", "license"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            diagnostics.append(_field_diagnostic("invalid_metadata", f"metadata.{key}", f"metadata.{key} must be a non-empty string"))
        optional[key] = item if isinstance(item, str) else None
    if any(item.field and item.field.startswith("metadata") for item in diagnostics):
        return None
    return ExtensionMetadata(
        name=name,
        description=description,
        author=optional["author"],
        homepage=optional["homepage"],
        license=optional["license"],
        extensions={key: item for key, item in value.items() if key.startswith("x-")},
    )


def _validate_contributions(
    value: object, diagnostics: list[ExtensionDiagnostic]
) -> ExtensionContributionDeclaration | None:
    if not isinstance(value, dict):
        diagnostics.append(
            _field_diagnostic("invalid_contributions", "contributions", "contributions must be an object")
        )
        return None
    _reject_unknown_fields(value, _CONTRIBUTION_FIELDS, "contributions", diagnostics)
    parsed: dict[str, tuple[str, ...]] = {}
    for key in ("data_sources", "widgets"):
        field_name = f"contributions.{key}"
        items = value.get(key, [])
        if not isinstance(items, list):
            diagnostics.append(
                _field_diagnostic("invalid_contributions", field_name, f"{field_name} must be an array")
            )
            continue
        if any(not isinstance(item, str) or _CONTRIBUTION_ID_RE.fullmatch(item) is None for item in items):
            diagnostics.append(
                _field_diagnostic("invalid_contributions", field_name, f"{field_name} contains an invalid id")
            )
            continue
        if len(set(items)) != len(items):
            diagnostics.append(
                _field_diagnostic("invalid_contributions", field_name, f"{field_name} must not contain duplicates")
            )
            continue
        parsed[key] = tuple(items)
    if "data_sources" not in parsed or "widgets" not in parsed:
        return None
    return ExtensionContributionDeclaration(parsed["data_sources"], parsed["widgets"])


def _validate_requirements(
    value: object,
    extension_id: str | None,
    diagnostics: list[ExtensionDiagnostic],
) -> list[ExtensionRequirement]:
    if not isinstance(value, list):
        diagnostics.append(_field_diagnostic("invalid_requires", "requires", "requires must be an array"))
        return []
    requirements: list[ExtensionRequirement] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"requires[{index}]"
        if not isinstance(item, dict):
            diagnostics.append(_field_diagnostic("invalid_requirement", prefix, "requirement must be an object"))
            continue
        _reject_unknown_fields(item, _REQUIREMENT_FIELDS, prefix, diagnostics)
        requirement_id = item.get("id")
        minimum = item.get("min_version", "0.0.0")
        valid = True
        if not isinstance(requirement_id, str) or _EXTENSION_ID_RE.fullmatch(requirement_id) is None:
            diagnostics.append(_field_diagnostic("invalid_requirement", f"{prefix}.id", "requirement id is invalid"))
            valid = False
        elif requirement_id == extension_id:
            diagnostics.append(_field_diagnostic("invalid_requirement", f"{prefix}.id", "extension cannot require itself"))
            valid = False
        elif requirement_id in seen:
            diagnostics.append(_field_diagnostic("invalid_requirement", f"{prefix}.id", "requirement id is duplicated"))
            valid = False
        if not is_valid_semver(minimum):
            diagnostics.append(
                _field_diagnostic("invalid_requirement", f"{prefix}.min_version", "minimum version is invalid")
            )
            valid = False
        if valid:
            seen.add(requirement_id)
            requirements.append(ExtensionRequirement(requirement_id, minimum))
    return requirements


def _validate_backend(
    value: object,
    package_root: Path,
    diagnostics: list[ExtensionDiagnostic],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        diagnostics.append(
            _field_diagnostic("invalid_backend", "backend", "backend must be an object")
        )
        return None
    _reject_unknown_fields(value, _BACKEND_FIELDS, "backend", diagnostics)
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
        diagnostics.append(
            _field_diagnostic(
                "invalid_backend", "backend.entrypoint", "entrypoint must be module:symbol"
            )
        )
        return None
    module_name, symbol = entrypoint.split(":", 1)
    if (
        _BACKEND_SYMBOL_RE.fullmatch(module_name) is None
        or _BACKEND_SYMBOL_RE.fullmatch(symbol) is None
    ):
        diagnostics.append(
            _field_diagnostic(
                "invalid_backend", "backend.entrypoint", "entrypoint must be module:symbol"
            )
        )
        return None
    module_parts = module_name.split(".")
    candidates = (
        package_root.joinpath(*module_parts).with_suffix(".py"),
        package_root.joinpath(*module_parts, "__init__.py"),
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if _is_within(resolved, package_root) and resolved.is_file():
            return entrypoint
    diagnostics.append(
        _field_diagnostic(
            "path_not_found", "backend.entrypoint", "backend module does not exist"
        )
    )
    return None


def _validate_frontend(
    value: object,
    package_root: Path,
    diagnostics: list[ExtensionDiagnostic],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        diagnostics.append(
            _field_diagnostic("invalid_frontend", "frontend", "frontend must be an object")
        )
        return None
    _reject_unknown_fields(value, _FRONTEND_FIELDS, "frontend", diagnostics)
    module = value.get("module")
    validated = _validate_entry_path(
        module,
        field_name="frontend.module",
        package_root=package_root,
        allow_symbol=False,
        diagnostics=diagnostics,
    )
    if validated is None:
        return None
    relative = PurePosixPath(validated)
    if not relative.parts or relative.parts[0] != "frontend" or relative.suffix not in {".js", ".mjs"}:
        diagnostics.append(
            _field_diagnostic(
                "invalid_frontend",
                "frontend.module",
                "frontend module must be a .js or .mjs file inside frontend/",
            )
        )
        return None
    return validated


def _validate_entry_path(
    value: object,
    *,
    field_name: str,
    package_root: Path,
    allow_symbol: bool,
    diagnostics: list[ExtensionDiagnostic],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} must be a non-empty string"))
        return None
    path_value = value
    if "\\" in path_value or "://" in path_value:
        diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} must use a relative POSIX path"))
        return None
    symbol: str | None = None
    if allow_symbol and ":" in path_value:
        if path_value.count(":") != 1:
            diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} has an invalid symbol suffix"))
            return None
        path_value, symbol = path_value.split(":", 1)
        if not symbol or _BACKEND_SYMBOL_RE.fullmatch(symbol) is None:
            diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} has an invalid symbol suffix"))
            return None
    elif ":" in path_value:
        diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} must not contain a URL or drive prefix"))
        return None

    relative = PurePosixPath(path_value)
    if not path_value or relative.is_absolute() or ".." in relative.parts:
        diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} must remain inside the package"))
        return None
    candidate = package_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        diagnostics.append(_field_diagnostic("path_not_found", field_name, f"{field_name} does not exist"))
        return None
    if not _is_within(resolved, package_root):
        diagnostics.append(_field_diagnostic("path_escape", field_name, f"{field_name} escapes the package"))
        return None
    if not resolved.is_file():
        diagnostics.append(_field_diagnostic("invalid_path", field_name, f"{field_name} must reference a file"))
        return None
    return value


def _reject_unknown_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    prefix: str,
    diagnostics: list[ExtensionDiagnostic],
) -> None:
    for key in sorted(value):
        if key in allowed or key.startswith("x-"):
            continue
        field_name = f"{prefix}.{key}" if prefix else key
        diagnostics.append(_field_diagnostic("unknown_field", field_name, "ordinary unknown fields are not allowed"))


def _mark_duplicate_ids(
    records: Iterable[ExtensionDiscoveryRecord],
) -> list[ExtensionDiscoveryRecord]:
    result = list(records)
    positions: dict[str, list[int]] = {}
    for index, record in enumerate(result):
        if record.extension_id is not None:
            positions.setdefault(record.extension_id, []).append(index)
    for extension_id in sorted(positions):
        duplicate_positions = positions[extension_id]
        if len(duplicate_positions) < 2:
            continue
        for index in duplicate_positions:
            record = result[index]
            duplicate = ExtensionDiagnostic(
                "duplicate_id",
                "extension id is declared by more than one package",
                source=record.source,
                package_root=record.package_root,
                manifest_path=record.manifest_path,
                extension_id=extension_id,
                field="id",
            )
            result[index] = replace(record, diagnostics=record.diagnostics + (duplicate,))
    return result


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _field_diagnostic(code: str, field: str, message: str) -> ExtensionDiagnostic:
    return ExtensionDiagnostic(code, message, field=field)


def _diagnostic(
    code: str,
    message: str,
    *,
    source: str,
    package_root: Path,
    manifest_path: Path,
) -> ExtensionDiagnostic:
    return ExtensionDiagnostic(
        code,
        message,
        source=source,
        package_root=package_root,
        manifest_path=manifest_path,
    )
