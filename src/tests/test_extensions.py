"""End-to-end host-managed extension activation and route tests."""

from __future__ import annotations

from app.factory import create_app

_EXTENSION_ID = "com.cuckoo.runtime-health"
_WIDGET_TYPE = "com.cuckoo.runtime-health.card"


def _config(tmp_path):
    return {
        "TESTING": True,
        "WORKSPACE_DATABASE": str(tmp_path / "workspaces.db"),
        "EXTENSION_DATABASE": str(tmp_path / "extensions.db"),
        "EXTENSION_DATA_DIR": str(tmp_path / "user-extensions"),
    }


def _loopback():
    return {"REMOTE_ADDR": "127.0.0.1"}


def _mutation_headers():
    return {"Origin": "http://localhost"}


def test_extension_enable_requires_restart_and_catalog_serves_only_active_assets(tmp_path):
    config = _config(tmp_path)
    first = create_app(config)
    client = first.test_client()

    initial = client.get(
        "/api/settings/extensions", environ_base=_loopback()
    ).get_json()
    optional = next(item for item in initial["extensions"] if item["id"] == _EXTENSION_ID)
    assert optional["status"] == "disabled"
    assert optional["desired_enabled"] is False
    assert optional["effective_enabled"] is False
    assert first.extensions["workspace_registry"].data_source_ids() == (
        "system.snapshot",
        "media.playback",
        "github.contributions",
        "dashboard.aggregate",
    )

    enabled = client.put(
        f"/api/settings/extensions/{_EXTENSION_ID}",
        json={"revision": initial["revision"], "desired_enabled": True},
        headers=_mutation_headers(),
        environ_base=_loopback(),
    )
    assert enabled.status_code == 200
    assert enabled.get_json()["extension"]["status"] == "pending_enable"
    assert enabled.get_json()["extension"]["restart_required"] is True
    assert all(
        item["id"] != _EXTENSION_ID
        for item in client.get("/api/runtime/extensions").get_json()["extensions"]
    )

    restarted = create_app(config)
    restarted_client = restarted.test_client()
    catalog = restarted_client.get("/api/runtime/extensions").get_json()
    extension = next(item for item in catalog["extensions"] if item["id"] == _EXTENSION_ID)
    assert extension["widget_types"] == [_WIDGET_TYPE]
    assert extension["module_url"].startswith(
        f"/runtime/extensions/{_EXTENSION_ID}/assets/index.js"
    )
    asset = restarted_client.get(extension["module_url"])
    assert asset.status_code == 200
    assert b"registerCuckooExtension" in asset.data
    assert restarted_client.get(
        f"/runtime/extensions/{_EXTENSION_ID}/assets/%2e%2e/backend.py"
    ).status_code == 404

    collection = restarted_client.get(
        "/api/settings/workspaces", environ_base=_loopback()
    ).get_json()
    assert _WIDGET_TYPE in {item["type"] for item in collection["widget_catalog"]}


def test_extension_disable_is_blocked_by_workspace_references(tmp_path):
    config = _config(tmp_path)
    first = create_app(config)
    first_client = first.test_client()
    initial = first_client.get(
        "/api/settings/extensions", environ_base=_loopback()
    ).get_json()
    enabled = first_client.put(
        f"/api/settings/extensions/{_EXTENSION_ID}",
        json={"revision": initial["revision"], "desired_enabled": True},
        headers=_mutation_headers(),
        environ_base=_loopback(),
    ).get_json()
    state_revision = enabled["revision"]

    app = create_app(config)
    client = app.test_client()
    created = client.post(
        "/api/settings/workspaces",
        json={"name": "Health Workspace", "id": "health-workspace"},
        headers=_mutation_headers(),
        environ_base=_loopback(),
    ).get_json()
    saved = client.put(
        "/api/settings/workspaces/health-workspace",
        json={
            "revision": created["revision"],
            "name": created["name"],
            "grid": created["grid"],
            "widgets": [
                {
                    "id": "health-card",
                    "type": _WIDGET_TYPE,
                    "owner": _EXTENSION_ID,
                    "slot": "main",
                    "layout": {"x": 0, "y": 0, "width": 4, "height": 3},
                    "constraints": {
                        "min_width": 4,
                        "min_height": 3,
                        "max_width": 16,
                        "max_height": 15,
                    },
                }
            ],
        },
        headers=_mutation_headers(),
        environ_base=_loopback(),
    )
    assert saved.status_code == 200
    assert saved.get_json()["widgets"][0]["owner"] == _EXTENSION_ID

    blocked = client.put(
        f"/api/settings/extensions/{_EXTENSION_ID}",
        json={"revision": state_revision, "desired_enabled": False},
        headers=_mutation_headers(),
        environ_base=_loopback(),
    )
    assert blocked.status_code == 409
    error = blocked.get_json()["error"]
    assert error["code"] == "extension_in_use"
    assert error["references"][0]["workspace_id"] == "health-workspace"

    removed = client.put(
        "/api/settings/workspaces/health-workspace",
        json={
            "revision": saved.get_json()["revision"],
            "name": "Health Workspace",
            "grid": {"columns": 16, "rows": 15},
            "widgets": [],
        },
        headers=_mutation_headers(),
        environ_base=_loopback(),
    )
    assert removed.status_code == 200
    disabled = client.put(
        f"/api/settings/extensions/{_EXTENSION_ID}",
        json={"revision": state_revision, "desired_enabled": False},
        headers=_mutation_headers(),
        environ_base=_loopback(),
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["extension"]["status"] == "pending_disable"

    restarted = create_app(config)
    assert all(
        item["id"] != _EXTENSION_ID
        for item in restarted.test_client().get("/api/runtime/extensions").get_json()["extensions"]
    )


def test_extension_settings_routes_keep_loopback_and_host_protection(tmp_path):
    client = create_app(_config(tmp_path)).test_client()
    assert client.get(
        "/api/settings/extensions", environ_base={"REMOTE_ADDR": "192.168.1.5"}
    ).status_code == 403
    assert client.get(
        "/api/settings/extensions",
        headers={"Host": "attacker.example"},
        environ_base=_loopback(),
    ).status_code == 403
