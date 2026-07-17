"""AST 依赖边界：契约向下稳定，业务层不绑定具体 Provider。"""

from __future__ import annotations

import ast
from pathlib import Path


SRC = Path(__file__).resolve().parents[1]
_BUILTIN_PROVIDERS = {"providers.mimo", "providers.nug", "providers.local_platform"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _depends_on(imported: str, roots: set[str]) -> bool:
    return any(imported == root or imported.startswith(f"{root}.") for root in roots)


def test_core_does_not_depend_on_upper_layers():
    forbidden = {"providers", "services", "features", "app", "runtime"}
    for path in (SRC / "core").rglob("*.py"):
        assert not any(_depends_on(name, forbidden) for name in _imports(path)), path


def test_providers_do_not_depend_on_services_features_app_or_runtime():
    forbidden = {"services", "features", "app", "runtime"}
    for path in (SRC / "providers").rglob("*.py"):
        assert not any(_depends_on(name, forbidden) for name in _imports(path)), path


def test_services_do_not_import_concrete_providers():
    for path in (SRC / "services").rglob("*.py"):
        assert not any(
            _depends_on(imported, _BUILTIN_PROVIDERS) for imported in _imports(path)
        ), path


def test_app_and_feature_routes_do_not_import_concrete_providers():
    paths = list((SRC / "app").rglob("*.py"))
    paths.extend((SRC / "features").rglob("routes.py"))
    for path in paths:
        assert not any(
            _depends_on(imported, _BUILTIN_PROVIDERS) for imported in _imports(path)
        ), path


def test_contracts_only_depend_on_standard_library_or_other_contracts():
    allowed_roots = {
        "__future__",
            "collections",
            "copy",
            "dataclasses",

        "typing",
        "contracts",
    }
    for path in (SRC / "contracts").rglob("*.py"):
        imports = _imports(path)
        assert not {
            imported
            for imported in imports
            if imported.split(".", 1)[0] not in allowed_roots
        }, path
