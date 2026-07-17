"""Static ES Module entry, import graph, and protected Settings asset tests."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.factory import create_app
from workspaces.builtins import create_builtin_workspace_registry

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
EXPECTED_DASHBOARD_WIDGET_TYPES = {
    "builtin.dashboard.system-info",
    "builtin.dashboard.network",
    "builtin.dashboard.uptime",
    "builtin.dashboard.disks",
    "builtin.dashboard.vibe",
    "builtin.dashboard.player",
    "builtin.dashboard.github",
}


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


def test_dashboard_html_keeps_shell_and_delegates_builtin_cards_to_workspace_host():
    source = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="workspaceHost"' in source
    assert 'class="hdr"' in source
    for marker in (
        'id="vibeCard"',
        'id="sysCard"',
        'id="diskCard"',
        'class="card netCard"',
        'class="card uptimeCard"',
        'id="lyricCard"',
        'id="ghGrid"',
    ):
        assert marker not in source


def test_dashboard_builtin_type_allowlist_matches_backend_registry():
    workspace = STATIC / "modules" / "dashboard" / "workspace"
    registry_source = (workspace / "registry.js").read_text(encoding="utf-8")
    core_package_source = (workspace / "core-package.js").read_text(encoding="utf-8")
    fallback_source = (workspace / "default-manifest.js").read_text(encoding="utf-8")
    frontend_types = set(re.findall(r"registerWidget\('(builtin\.[^']+)'", core_package_source))
    fallback_types = set(re.findall(r"widget\('[^']+',\s*'(builtin\.[^']+)'", fallback_source))
    backend_types = {
        widget.type
        for widget in create_builtin_workspace_registry().get_workspace("main").widgets
    }
    assert frontend_types == fallback_types == backend_types == EXPECTED_DASHBOARD_WIDGET_TYPES
    assert "import(" not in registry_source


def test_frontend_fallback_manifest_matches_builtin_backend_manifest():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js 不可用")
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import('./src/static/modules/dashboard/workspace/default-manifest.js')"
            ".then((module) => process.stdout.write(JSON.stringify(module.DEFAULT_WORKSPACE_MANIFEST)))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    frontend = json.loads(result.stdout)
    backend = create_builtin_workspace_registry().serialize_workspace("main")
    assert frontend == backend


def test_workspace_components_preserve_legacy_dom_contract():
    components = STATIC / "modules" / "dashboard" / "workspace" / "components"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in components.glob("*.js"))
    for marker in (
        "sysCard", "dot-system", "sysMain", "diskCard", "sysDisks", "netUp", "netDown",
        "uptimeVal", "lyricCard", "dot-media", "lyricText", "lyricScroll", "lyricIdle",
        "lyricTitle", "lyricOffsetVal", "lyricArtist", "dot-github", "ghGrid", "ghUser", "ghTotal",
        "vibeCard", "dot-vibe", "vibeToggle", "ringFg", "todayTotal", "vibeBalances",
        'data-action="player-control"', 'data-action="adjust-lyric-offset"', 'data-action="toggle-vibe"',
    ):
        assert marker in combined


def test_dashboard_optional_channels_follow_workspace_manifest():
    dashboard = STATIC / "modules" / "dashboard"
    main_source = (dashboard / "main.js").read_text(encoding="utf-8")
    ws_source = (dashboard / "ws.js").read_text(encoding="utf-8")
    music_ws = (STATIC / "modules" / "music" / "ws.js").read_text(encoding="utf-8")

    assert "types.has('builtin.dashboard.player')" in main_source
    assert "activeSubscriptionClient.sendReplace()" in ws_source
    state_start = ws_source.index("function sendWorkspaceState")
    assert ws_source.index("activeSubscriptionClient.sendReplace()", state_start) < ws_source.index(
        "sendWorkspaceViewport(socket)", state_start
    )
    assert "viewport: getWorkspaceViewport()" in ws_source
    assert "message.type === 'data.snapshot'" in ws_source
    assert "subscriptions.routeSnapshot(message)" in ws_source
    assert "publishSource('media.lyric'" in ws_source
    assert "sources: activeSources" not in ws_source
    assert "sources: ['media.playback']" in music_ws
    assert music_ws.index("sources: ['media.playback']") < music_ws.index("type: 'init'")


def test_dashboard_workspace_v3_layout_and_loading_contracts():
    dashboard = STATIC / "modules" / "dashboard"
    workspace = dashboard / "workspace"
    css = (STATIC / "dashboard.css").read_text(encoding="utf-8")
    manifest = (workspace / "default-manifest.js").read_text(encoding="utf-8")
    host = (workspace / "host.js").read_text(encoding="utf-8")
    main = (dashboard / "main.js").read_text(encoding="utf-8")
    api = (dashboard / "api.js").read_text(encoding="utf-8")
    ws = (dashboard / "ws.js").read_text(encoding="utf-8")
    vibe = (dashboard / "vibe.js").read_text(encoding="utf-8")

    assert "grid-template-rows:44px minmax(0,1fr)" in css
    assert "#workspaceHost" in css
    assert "--workspace-surface-width" in css
    assert "--workspace-surface-height" in css
    assert "grid-template-columns:repeat(16" not in css
    assert "grid-template-rows:repeat(15" not in css
    for fixed_position in (
        ".sysCard { grid-row:", ".netCard { grid-row:", ".uptimeCard { grid-row:",
        ".diskCard { grid-row:", ".tokenCard { grid-row:", ".lyric-wrap { grid-row:",
        ".gh-wrap { grid-row:",
    ):
        assert fixed_position not in css

    assert "version: 3" in manifest
    assert "revision: 1" in manifest
    assert "columns: 16" in manifest
    assert "rows: 15" in manifest
    assert "calibration:" in manifest
    assert "reference_width: 1920" in manifest
    for layout in (
        "{ x: 0, y: 0, width: 6, height: 5 }",
        "{ x: 6, y: 0, width: 2, height: 3 }",
        "{ x: 6, y: 3, width: 2, height: 2 }",
        "{ x: 0, y: 5, width: 8, height: 4 }",
        "{ x: 8, y: 0, width: 8, height: 9 }",
        "{ x: 0, y: 9, width: 8, height: 6 }",
        "{ x: 8, y: 9, width: 8, height: 6 }",
    ):
        assert layout in manifest
    assert "revision: manifest.revision" in host
    assert "manifest.grid.calibration must be an object" in host
    assert "manifest.name must be a non-empty string" in host
    assert "createViewportCalibration" in host
    assert "layout: widget.layout" in host
    assert "element.style.gridColumn" in host
    assert "element.style.gridRow" in host
    assert "stagingRoot" in host

    assert "workspaceIdFromPath" in main
    assert "workspaceId !== 'main'" in main
    assert "workspaceRequestSequence" in main
    assert "showWorkspaceError" in main
    assert "fetchWorkspaceManifest(workspaceId)" in main
    assert "encodeURIComponent(normalizedId)" in api
    assert "case 'workspace_updated'" in ws
    assert "workspace_id: activeWorkspaceId" in ws
    assert "reconcileWorkspace('websocket_open')" in ws
    assert "socket.send(JSON.stringify({ type: 'init' }))" in ws
    assert "handleDashboardData" not in ws
    assert "dashboardDataBus.publish('dashboard.aggregate'" in vibe


def test_dashboard_components_return_roots_and_vibe_owns_token_dom():
    components = STATIC / "modules" / "dashboard" / "workspace" / "components"
    for name in ("system-info.js", "network.js", "uptime.js", "disks.js", "player.js", "github.js", "vibe.js"):
        source = (components / name).read_text(encoding="utf-8")
        assert "context.root.appendChild(root);" in source, name
        assert "context.subscribe(" in source, name
        assert "update(" in source, name
        assert "onData(" not in source, name
        assert "return root;" in source, name
    vibe = (components / "vibe.js").read_text(encoding="utf-8")
    assert "root.id = 'vibeCard'" in vibe
    assert "handleDashboardData(payload" in vibe


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
            encoding="utf-8",
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


def test_workspace_data_bus_and_host_basic_behavior_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js 不可用")
    script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const modules = new Map();
async function moduleFor(file) {
  const absolute = path.resolve(file);
  if (modules.has(absolute)) return modules.get(absolute);
  const mod = new vm.SourceTextModule(fs.readFileSync(absolute, 'utf8'), { identifier: absolute });
  modules.set(absolute, mod);
  await mod.link((specifier, referencing) => {
    if (!specifier.startsWith('.')) throw new Error('unexpected import ' + specifier);
    return moduleFor(path.resolve(path.dirname(referencing.identifier), specifier));
  });
  return mod;
}
async function load(file) {
  const mod = await moduleFor(file);
  if (mod.status !== 'evaluated') await mod.evaluate();
  return mod.namespace;
}
class FakeElement {
  constructor(ownerDocument) {
    this.ownerDocument = ownerDocument;
    this.nodeType = 1;
    this.parentNode = null;
    this.childNodes = [];
    this.dataset = {};
    this.style = {};
  }
  appendChild(child) {
    child.remove();
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.childNodes.slice().forEach((child) => child.remove());
    children.forEach((child) => this.appendChild(child));
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.childNodes;
    const index = siblings.indexOf(this);
    if (index >= 0) siblings.splice(index, 1);
    this.parentNode = null;
  }
}
const fakeDocument = { createElement() { return new FakeElement(fakeDocument); } };
global.document = fakeDocument;
(async () => {
  const busModule = await load('src/static/modules/dashboard/workspace/data-bus.js');
  const hostModule = await load('src/static/modules/dashboard/workspace/host.js');
  const bus = new busModule.DataBus();
  const seen = [];
  const unsubscribe = bus.subscribe('system.snapshot', (payload, source) => seen.push([source, payload.value]));
  if (bus.publish('system.snapshot', { value: 1 }) !== 1) throw new Error('publish count');
  unsubscribe();
  bus.publish('system.snapshot', { value: 2 });
  if (JSON.stringify(seen) !== JSON.stringify([['system.snapshot', 1]])) throw new Error('unsubscribe');
  if (!bus.hasLatest('system.snapshot') || bus.latest('system.snapshot').value !== 2) throw new Error('latest payload cache');
  let isolated = 0;
  bus.subscribe('isolated.source', () => { throw new Error('expected subscriber failure'); });
  bus.subscribe('isolated.source', () => { isolated += 1; });
  const originalConsoleError = console.error;
  console.error = () => {};
  const isolatedCount = bus.publish('isolated.source', {});
  console.error = originalConsoleError;
  if (isolatedCount !== 2 || isolated !== 1) throw new Error('subscriber isolation');

  let mounts = 0;
  let updates = 0;
  let destroys = 0;
  const registry = {
    has(type) { return type === 'test.widget'; },
    get(type) {
      if (!this.has(type)) return null;
      return { create() {
        let element = null;
        return {
          mount(context) {
            mounts += 1;
            element = fakeDocument.createElement('div');
            context.root.appendChild(element);
            if (context.widget.id === 'fail') throw new Error('expected mount failure');
            return element;
          },
          onData(payload) { updates += payload.value; },
          destroy() { destroys += 1; element?.remove(); },
        };
      } };
    },
  };
  const root = fakeDocument.createElement('main');
  const host = new hostModule.WorkspaceHost({ root, registry, bus });
  const baseWidget = {
    id: 'one', type: 'test.widget', slot: 'main',
    sources: ['system.snapshot'], channels: ['media.lyric'],
    layout: { x: 0, y: 0, width: 2, height: 2 },
    constraints: { min_width: 1, min_height: 1, max_width: 16, max_height: 15 },
  };
  const manifest = {
    id: 'test', version: 3, revision: 1, name: 'Test', kind: 'custom', required: true,
    grid: {
      columns: 16, rows: 15,
      calibration: {
        reference_width: 1920, reference_height: 1080,
        target_cell_width: 120, target_cell_height: 72,
        fit_mode: 'contain', density: 'normal',
      },
    },
    sources: [{ id: 'dashboard.aggregate' }],
    widgets: [baseWidget],
  };
  const first = host.mount(manifest);
  host.mount(JSON.parse(JSON.stringify(manifest)));
  if (mounts !== 1 || root.childNodes.length !== 1) throw new Error('idempotent mount');
  if (updates !== 2) throw new Error('initial replay');
  if (root.childNodes[0].childNodes[0].style.gridColumn !== '1 / span 2') throw new Error('grid column');
  if (root.childNodes[0].childNodes[0].style.gridRow !== '1 / span 2') throw new Error('grid row');
  if (JSON.stringify(first.sources.sort()) !== JSON.stringify(['dashboard.aggregate', 'system.snapshot'])) throw new Error('sources');
  if (JSON.stringify(first.channels) !== JSON.stringify(['media.lyric'])) throw new Error('channels');

  const revised = host.mount({ ...manifest, revision: 2 });
  if (mounts !== 2 || destroys !== 1 || revised.revision !== 2 || updates !== 4) throw new Error('revision did not remount with replay');
  const moved = host.mount({
    ...manifest,
    revision: 3,
    widgets: [{ ...baseWidget, layout: { x: 2, y: 0, width: 2, height: 2 } }],
  });
  if (mounts !== 3 || destroys !== 2 || updates !== 6) throw new Error('layout did not remount with replay');
  if (root.childNodes[0].childNodes[0].style.gridColumn !== '3 / span 2') throw new Error('updated grid column');
  bus.publish('system.snapshot', { value: 3 });
  if (updates !== 9) throw new Error('active subscription');

  const stableNode = root.childNodes[0];
  const stableSubscriptions = JSON.stringify(host.summary().subscriptions);
  let failed = false;
  try {
    host.mount({ ...manifest, revision: 4, widgets: [{ ...baseWidget, id: 'fail' }] });
  } catch (_error) { failed = true; }
  if (!failed || root.childNodes[0] !== stableNode || host.summary().revision !== moved.revision
      || JSON.stringify(host.summary().subscriptions) !== stableSubscriptions) {
    throw new Error('failed mount replaced stable workspace or subscriptions');
  }
  let rejected = false;
  try {
    host.mount({
      ...manifest,
      revision: 5,
      widgets: [{ ...baseWidget, constraints: { ...baseWidget.constraints, min_width: 3 } }],
    });
  } catch (_error) { rejected = true; }
  if (!rejected) throw new Error('invalid constraints accepted');
  const unavailable = host.mount({
    ...manifest,
    revision: 6,
    widgets: [{ ...baseWidget, type: 'unknown.widget', available: false, unavailable_reason: 'extension_missing' }],
  });
  if (unavailable.revision !== 6 || unavailable.unavailableWidgetIds[0] !== 'one') {
    throw new Error('unknown type placeholder failed');
  }
  if (JSON.stringify(unavailable.sources) !== JSON.stringify(['dashboard.aggregate'])) {
    throw new Error('unavailable widget source was subscribed');
  }

  host.destroy();
  bus.publish('system.snapshot', { value: 4 });
  if (root.childNodes.length !== 0 || updates !== 9) throw new Error('destroy');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        [node, "--experimental-vm-modules", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
