"""Extension Manifest discovery and desired-state persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extensions.discovery import discover_extensions
from extensions.repository import (
    ExtensionRevisionConflictError,
    ExtensionStateRepository,
)


def _write_extension(
    root: Path,
    extension_id: str,
    *,
    api_version: int = 1,
    frontend_module: str = "frontend/index.js",
    backend_entrypoint: str = "backend:create_extension",
) -> Path:
    package = root / extension_id
    (package / "frontend").mkdir(parents=True)
    (package / "backend.py").write_text(
        "def create_extension(context):\n    raise RuntimeError('discovery imported backend')\n",
        encoding="utf-8",
    )
    (package / "frontend" / "index.js").write_text(
        "export function registerCuckooExtension() {}\n", encoding="utf-8"
    )
    manifest = {
        "manifest_version": 1,
        "id": extension_id,
        "version": "1.2.3",
        "api_version": api_version,
        "metadata": {"name": extension_id},
        "backend": {"entrypoint": backend_entrypoint},
        "frontend": {"module": frontend_module},
        "contributions": {
            "data_sources": [f"{extension_id}.source"],
            "widgets": [f"{extension_id}.widget"],
        },
        "requires": [],
    }
    (package / "extension.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return package


def test_discovery_is_import_free_and_validates_nested_manifest(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    package = _write_extension(builtin, "com.example.health")
    marker = package / "imported.txt"
    (package / "backend.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )

    result = discover_extensions(builtin, user)

    assert [(record.id, record.valid) for record in result.records] == [
        ("com.example.health", True)
    ]
    assert result.manifests[0].api_version == 1
    assert result.manifests[0].backend_entrypoint == "backend:create_extension"
    assert result.manifests[0].frontend_module == "frontend/index.js"
    assert marker.exists() is False
    assert user.exists() is False


def test_duplicate_ids_and_unsafe_paths_are_rejected(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_extension(builtin, "com.example.duplicate")
    _write_extension(user, "com.example.duplicate")
    unsafe = _write_extension(builtin, "com.example.unsafe")
    payload = json.loads((unsafe / "extension.json").read_text(encoding="utf-8"))
    payload["frontend"]["module"] = "../outside.js"
    (unsafe / "extension.json").write_text(json.dumps(payload), encoding="utf-8")

    result = discover_extensions(builtin, user)
    duplicate_records = [
        record for record in result.records if record.id == "com.example.duplicate"
    ]
    unsafe_record = next(record for record in result.records if record.id == "com.example.unsafe")

    assert len(duplicate_records) == 2
    assert all(not record.valid for record in duplicate_records)
    assert all("duplicate_id" in {item.code for item in record.diagnostics} for record in duplicate_records)
    assert unsafe_record.valid is False
    assert {item.code for item in unsafe_record.diagnostics} & {"invalid_path", "path_not_found"}


def test_api_version_and_contribution_prefix_are_strict(tmp_path):
    builtin = tmp_path / "builtin"
    package = _write_extension(builtin, "com.example.invalid", api_version=2)
    payload = json.loads((package / "extension.json").read_text(encoding="utf-8"))
    payload["contributions"]["widgets"] = ["other.widget"]
    (package / "extension.json").write_text(json.dumps(payload), encoding="utf-8")

    record = discover_extensions(builtin, tmp_path / "missing").records[0]

    assert record.valid is False
    assert {item.code for item in record.diagnostics} >= {
        "incompatible_api",
        "invalid_contribution_prefix",
    }


def test_extension_state_repository_is_lazy_and_uses_global_revision_cas(tmp_path):
    database = tmp_path / "extensions.db"
    repository = ExtensionStateRepository(database)

    assert repository.list_snapshot().revision == 0
    assert database.exists() is False
    first = repository.set_desired(
        "com.example.health", True, expected_revision=0
    )
    assert first.revision == 1
    assert first.states == {"com.example.health": True}
    assert database.exists() is True

    with pytest.raises(ExtensionRevisionConflictError) as conflict:
        repository.set_desired(
            "com.example.health", False, expected_revision=0
        )
    assert conflict.value.current_revision == 1

    second = repository.set_desired(
        "com.example.health", False, expected_revision=1
    )
    assert second.revision == 2
    assert second.states["com.example.health"] is False
