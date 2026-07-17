"""Extension dependency ordering and lifecycle isolation tests."""

from __future__ import annotations

import json
from pathlib import Path

from extensions.manager import ExtensionManager
from extensions.repository import ExtensionStateRepository
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.repository import WorkspaceRepository


def _write_lifecycle_extension(
    root: Path,
    extension_id: str,
    events: Path,
    *,
    requires: list[dict] | None = None,
) -> None:
    package = root / extension_id
    package.mkdir(parents=True)
    package.joinpath("backend.py").write_text(
        "from contracts.extension import ExtensionContributions\n"
        "from pathlib import Path\n"
        f"EVENTS = Path({str(events)!r})\n"
        f"EXTENSION_ID = {extension_id!r}\n"
        "class Extension:\n"
        "    def contributions(self):\n"
        "        return ExtensionContributions()\n"
        "    def start(self, context):\n"
        "        previous = EVENTS.read_text(encoding='utf-8') if EVENTS.exists() else ''\n"
        "        EVENTS.write_text(previous + EXTENSION_ID + ':start\\n', encoding='utf-8')\n"
        "    def stop(self, context, timeout):\n"
        "        previous = EVENTS.read_text(encoding='utf-8') if EVENTS.exists() else ''\n"
        "        EVENTS.write_text(previous + EXTENSION_ID + ':stop\\n', encoding='utf-8')\n"
        "def create_extension(context):\n"
        "    return Extension()\n",
        encoding="utf-8",
    )
    package.joinpath("extension.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "id": extension_id,
                "version": "1.0.0",
                "api_version": 1,
                "metadata": {"name": extension_id},
                "backend": {"entrypoint": "backend:create_extension"},
                "contributions": {"data_sources": [], "widgets": []},
                "requires": requires or [],
            }
        ),
        encoding="utf-8",
    )


def test_extension_lifecycle_uses_dependency_order_and_reverse_stop(tmp_path):
    root = tmp_path / "extensions"
    events = tmp_path / "events.txt"
    _write_lifecycle_extension(root, "com.example.base", events)
    _write_lifecycle_extension(
        root,
        "com.example.child",
        events,
        requires=[{"id": "com.example.base", "min_version": "1.0.0"}],
    )
    state = ExtensionStateRepository(":memory:")
    state.set_desired("com.example.base", True, expected_revision=0)
    state.set_desired("com.example.child", True, expected_revision=1)
    registry = create_builtin_workspace_registry()
    manager = ExtensionManager(
        registry,
        state,
        builtin_root=root,
        user_root=tmp_path / "missing",
        workspace_repository=WorkspaceRepository(":memory:"),
    ).prepare()

    manager.start_all()
    manager.stop_all(timeout=0)

    assert events.read_text(encoding="utf-8").splitlines() == [
        "com.example.base:start",
        "com.example.child:start",
        "com.example.child:stop",
        "com.example.base:stop",
    ]
    records = {item["id"]: item for item in manager.list_extensions()["extensions"]}
    assert records["com.example.base"]["effective_enabled"] is True
    assert records["com.example.child"]["effective_enabled"] is True


def test_dependency_cycle_isolated_without_importing_backends(tmp_path):
    root = tmp_path / "extensions"
    events = tmp_path / "events.txt"
    _write_lifecycle_extension(
        root,
        "com.example.first",
        events,
        requires=[{"id": "com.example.second", "min_version": "1.0.0"}],
    )
    _write_lifecycle_extension(
        root,
        "com.example.second",
        events,
        requires=[{"id": "com.example.first", "min_version": "1.0.0"}],
    )
    state = ExtensionStateRepository(":memory:")
    state.set_desired("com.example.first", True, expected_revision=0)
    state.set_desired("com.example.second", True, expected_revision=1)
    manager = ExtensionManager(
        create_builtin_workspace_registry(),
        state,
        builtin_root=root,
        user_root=tmp_path / "missing",
    ).prepare()

    records = {item["id"]: item for item in manager.list_extensions()["extensions"]}

    assert records["com.example.first"]["error"]["code"] == "dependency_cycle"
    assert records["com.example.second"]["error"]["code"] == "dependency_cycle"
    assert events.exists() is False
