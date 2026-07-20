"""Architecture tests that prevent concrete Provider dependencies from returning to the host."""

from __future__ import annotations

import ast
from pathlib import Path

from services.dashboard_data_service import build_dashboard_data


SRC = Path(__file__).resolve().parents[1]
_BUILTIN_PROVIDER_MODULES = {
    "providers.mimo",
    "providers.nug",
    "providers.nfk",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_core_never_imports_provider_layer():
    for path in (SRC / "core").glob("*.py"):
        imports = _imports(path)
        assert not any(name == "providers" or name.startswith("providers.") for name in imports), path


def test_host_entrypoints_never_import_builtin_provider_modules():
    for path in (SRC / "dashboard.py", SRC / "desktop.py"):
        imports = _imports(path)
        assert not any(
            imported == builtin or imported.startswith(f"{builtin}.")
            for imported in imports
            for builtin in _BUILTIN_PROVIDER_MODULES
        ), path


def test_legacy_provider_migration_surface_is_absent():
    assert not list((SRC / "providers").glob("**/migration.py"))
    for path in (
        SRC / "core" / "config.py",
        SRC / "services" / "config.py",
        SRC / "providers" / "base.py",
        SRC / "dashboard.py",
        SRC / "desktop.py",
        SRC / "providers" / "mimo" / "implementation.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "migrate_config" not in source, path
        assert "ensure_provider_migrations" not in source, path
        assert "plan_legacy_migration" not in source, path


def test_generic_dashboard_aggregates_unknown_provider_and_isolates_failures():
    class Alpha:
        CAPABILITIES = ["daily_usage"]

        def get_status(self):
            return {"status": "ok", "ok": True, "enabled": True}

        def get_today_usage(self):
            return {
                "input_tokens": 10,
                "output_tokens": 4,
                "cached_input_tokens": 3,
                "total_tokens": 14,
                "source_count": 1,
            }

    class Broken:
        CAPABILITIES = ["daily_usage"]

        def get_status(self):
            return {"status": "error", "ok": False, "enabled": True, "error": "offline"}

        def get_today_usage(self):
            raise RuntimeError("network unavailable")

    data = build_dashboard_data(providers={"alpha": Alpha(), "future-provider": Broken()})

    assert data["today"] == {"in": 10, "out": 4, "cache": 3, "total": 14, "inMiss": 7}
    assert data["provider_statuses"]["future-provider"]["status"] == "error"
    assert any(item["provider"] == "alpha" and item["ok"] for item in data["usage_sources"])
    assert any(item["provider"] == "future-provider" and not item["ok"] for item in data["usage_sources"])
