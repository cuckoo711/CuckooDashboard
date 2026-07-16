"""Static ES Module entry, import graph, and protected Settings asset tests."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.factory import create_app

STATIC = Path(__file__).resolve().parents[1] / "static"
MODULE_FILES = sorted((STATIC / "modules").rglob("*.js")) + sorted(
    (STATIC / "settings" / "modules").rglob("*.js")
)
ENTRY_FILES = [STATIC / "dashboard.js", STATIC / "music.js", STATIC / "settings.js"]
STATIC_IMPORT_RE = re.compile(
    r"(?:^|\n)\s*(?:import|export)\s+(?:[^'\"\n]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
DYNAMIC_IMPORT_RE = re.compile(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)")
INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def _import_targets(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    return STATIC_IMPORT_RE.findall(source) + DYNAMIC_IMPORT_RE.findall(source)


def _resolve_import(path: Path, target: str) -> Path | None:
    if target.startswith("/static/"):
        return STATIC / target.removeprefix("/static/")
    if target.startswith("/settings-assets/modules/"):
        return STATIC / "settings" / "modules" / target.removeprefix("/settings-assets/modules/")
    if target.startswith("."):
        return (path.parent / target).resolve()
    return None


def test_html_uses_module_entries_without_inline_handlers():
    for name in ("dashboard.html", "music.html", "settings.html"):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert not INLINE_HANDLER_RE.search(source), name
        assert 'type="module"' in source, name

    for name in ("dashboard.html", "music.html"):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert source.index("html2canvas.min.js") < source.index('type="module"'), name


def test_all_relative_and_absolute_module_imports_resolve():
    for path in MODULE_FILES + ENTRY_FILES:
        for target in _import_targets(path):
            resolved = _resolve_import(path, target)
            if resolved is not None:
                assert resolved.is_file(), (path, target, resolved)


def test_module_dependency_graph_is_acyclic():
    nodes = {path.resolve() for path in MODULE_FILES}
    graph: dict[Path, set[Path]] = {path: set() for path in nodes}
    for path in nodes:
        for target in _import_targets(path):
            resolved = _resolve_import(path, target)
            if resolved is not None and resolved.resolve() in nodes:
                graph[path].add(resolved.resolve())

    visiting: set[Path] = set()
    visited: set[Path] = set()

    def visit(path: Path) -> None:
        if path in visited:
            return
        assert path not in visiting, f"ES Module 循环依赖: {path}"
        visiting.add(path)
        for dependency in graph[path]:
            visit(dependency)
        visiting.remove(path)
        visited.add(path)

    for path in graph:
        visit(path)


def test_module_urls_are_served_and_settings_modules_stay_loopback_only():
    app = create_app({"TESTING": True})
    client = app.test_client()

    for path in sorted((STATIC / "modules").rglob("*.js")):
        url = "/static/" + path.relative_to(STATIC).as_posix()
        response = client.get(url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert response.status_code == 200, url
        assert "javascript" in response.content_type

    for path in sorted((STATIC / "settings" / "modules").rglob("*.js")):
        relative = path.relative_to(STATIC / "settings" / "modules").as_posix()
        url = "/settings-assets/modules/" + relative
        allowed = client.get(url, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        blocked = client.get(url, environ_base={"REMOTE_ADDR": "192.168.1.20"})
        assert allowed.status_code == 200, url
        assert "javascript" in allowed.content_type
        assert blocked.status_code == 403, url


@pytest.mark.parametrize(
    "path",
    [
        "/settings-assets/modules/../main.js",
        "/settings-assets/modules/%2e%2e/main.js",
        "/settings-assets/modules/main.css",
        "/settings-assets/modules/",
        "/settings-assets/settings.html",
        "/settings-assets/dashboard.js",
    ],
)
def test_settings_asset_allowlist_rejects_non_modules_and_traversal(path):
    response = create_app({"TESTING": True}).test_client().get(
        path,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 404


def test_all_authored_javascript_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js 不可用")
    for path in MODULE_FILES + ENTRY_FILES:
        result = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}\n{result.stdout}\n{result.stderr}"


def test_page_module_entries_link_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js 不可用")
    script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const promises = new Map();
function resolveSpec(spec, base) {
  if (spec.startsWith('/static/')) return path.resolve('src/static', spec.slice('/static/'.length));
  if (spec.startsWith('/settings-assets/modules/')) return path.resolve('src/static/settings/modules', spec.slice('/settings-assets/modules/'.length));
  if (spec.startsWith('.')) return path.resolve(path.dirname(base), spec);
  throw new Error('unsupported import ' + spec);
}
function load(file) {
  file = path.resolve(file);
  if (promises.has(file)) return promises.get(file);
  const promise = (async () => {
    const mod = new vm.SourceTextModule(fs.readFileSync(file, 'utf8'), { identifier: file });
    await mod.link((spec, ref) => load(resolveSpec(spec, ref.identifier)));
    return mod;
  })();
  promises.set(file, promise);
  return promise;
}
Promise.all([
  'src/static/modules/dashboard/main.js',
  'src/static/modules/music/main.js',
  'src/static/settings/modules/main.js',
].map(load)).catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        [node, "--experimental-vm-modules", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
