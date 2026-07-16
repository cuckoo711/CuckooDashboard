"""Settings workspace editor static contracts."""

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"
SETTINGS_MODULES = STATIC / "settings" / "modules"


def test_settings_page_contains_independent_workspace_editor_controls():
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    form_end = html.index("</form>")
    panel = html.index('id="workspacesPanel"')
    assert panel > form_end
    for marker in (
        'id="workspaceList"',
        'id="workspaceName"',
        'id="workspaceGridPreview"',
        'id="workspaceCatalog"',
        'id="workspaceSaveButton"',
        'id="workspaceDiscardButton"',
        'id="workspaceConflictBadge"',
    ):
        assert marker in html


def test_workspace_module_uses_expected_api_and_revision_guard():
    source = (SETTINGS_MODULES / "workspaces.js").read_text(encoding="utf-8")
    for marker in (
        "requestJson('/api/settings/workspaces')",
        "method: 'POST'",
        "method: 'PUT'",
        "method: 'DELETE'",
        "/duplicate",
        "revision: workspaceState.draft.revision",
        "error.status === 409",
        "setWorkspaceDirty(true)",
    ):
        assert marker in source
    assert "layout: {x: rect.x, y: rect.y, width: rect.w, height: rect.h}" in source
    assert "?revision=${encodeURIComponent(draft.revision)}" in source
    assert "collectionRequestSequence" in source
    assert "workspaceRequestSequence" in source
    assert "state.workspaceSaving" in source


def test_workspace_layout_uses_pointer_events_without_push_reflow():
    source = (SETTINGS_MODULES / "workspaces.js").read_text(encoding="utf-8")
    for event_name in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert event_name in source
    assert "firstFit(" in source
    assert "isPlacementValid(" in source
    assert "pushWidget" not in source
    assert "compactLayout" not in source


def test_config_and_workspace_dirty_state_are_separate_and_both_guard_unload():
    state_source = (SETTINGS_MODULES / "state.js").read_text(encoding="utf-8")
    main_source = (SETTINGS_MODULES / "main.js").read_text(encoding="utf-8")
    assert "dirty: false" in state_source
    assert "workspaceDirty: false" in state_source
    assert "state.dirty || state.workspaceDirty" in state_source
    assert "hasUnsavedChanges()" in main_source
    assert "beforeunload" in main_source


def test_client_navigation_supports_workspaces_and_legacy_music_page():
    source = (SETTINGS_MODULES / "clients.js").read_text(encoding="utf-8")
    assert "export function setClientWorkspaces(workspaces)" in source
    assert "{page: 'music'}" in source
    assert "{workspace_id:" in source
    assert "后端工作区导航尚未接入" not in source
