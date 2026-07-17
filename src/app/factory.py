"""Flask application factory for Cuckoo Dashboard."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Mapping

from flask import Flask
from flask_sock import Sock

from app.security import register_security_hooks
from core.config import DATA_DIR
from extensions.manager import ExtensionManager
from extensions.repository import ExtensionStateRepository
from features.appearance.routes import blueprint as appearance_blueprint
from features.dashboard.routes import blueprint as dashboard_blueprint
from features.extensions.routes import blueprint as extensions_blueprint
from features.media.routes import blueprint as media_blueprint
from features.music.routes import blueprint as music_blueprint
from features.providers.routes import blueprint as providers_blueprint
from features.providers.routes import register_provider_routes
from features.settings.extension_routes import blueprint as settings_extensions_blueprint
from features.settings.routes import blueprint as settings_blueprint
from features.settings.workspace_routes import blueprint as settings_workspaces_blueprint
from features.system.routes import blueprint as system_blueprint
from features.workspaces.routes import blueprint as workspaces_blueprint
from runtime.lifecycle import DashboardRuntime
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.repository import WorkspaceRepository

SRC_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = SRC_DIR / "static"


def create_app(
    test_config: Mapping[str, Any] | None = None,
    *,
    runtime: DashboardRuntime | None = None,
) -> Flask:
    """Create a fully routed Flask app without starting background workers."""
    app = Flask(
        "cuckoo_dashboard",
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
    if test_config:
        app.config.update(dict(test_config))
    if "WORKSPACE_DATABASE" not in app.config:
        app.config["WORKSPACE_DATABASE"] = (
            ":memory:" if app.config.get("TESTING") else str(DATA_DIR / "workspaces.db")
        )
    app.config.setdefault("EXTENSION_BUNDLED_DIR", str(SRC_DIR / "extensions"))
    app.config.setdefault(
        "EXTENSION_DATA_DIR",
        str(SRC_DIR / ".test-extensions-empty")
        if app.config.get("TESTING")
        else str(DATA_DIR / "extensions"),
    )
    app.config.setdefault(
        "EXTENSION_DATABASE",
        ":memory:" if app.config.get("TESTING") else str(DATA_DIR / "extensions.db"),
    )
    app.config.setdefault("EXTENSION_API_VERSION", 1)

    if runtime is None:
        mutation_lock = threading.RLock()
        workspace_registry = create_builtin_workspace_registry()
        workspace_repository = WorkspaceRepository(app.config["WORKSPACE_DATABASE"])
        extension_repository = ExtensionStateRepository(app.config["EXTENSION_DATABASE"])
        extension_manager = ExtensionManager(
            workspace_registry,
            extension_repository,
            builtin_root=app.config["EXTENSION_BUNDLED_DIR"],
            user_root=app.config["EXTENSION_DATA_DIR"],
            workspace_repository=workspace_repository,
            host_api_version=app.config["EXTENSION_API_VERSION"],
            mutation_lock=mutation_lock,
        ).prepare()
        dashboard_runtime = DashboardRuntime(
            workspace_registry=workspace_registry,
            workspace_repository=workspace_repository,
            workspace_database=app.config["WORKSPACE_DATABASE"],
            extension_manager=extension_manager,
        )
    else:
        dashboard_runtime = runtime
    dashboard_runtime.init_app(app)

    sock = Sock(app)
    app.extensions["dashboard_sock"] = sock
    dashboard_runtime.websocket.register(sock)

    register_security_hooks(app)
    app.register_blueprint(dashboard_blueprint)
    app.register_blueprint(settings_blueprint)
    app.register_blueprint(settings_workspaces_blueprint)
    app.register_blueprint(settings_extensions_blueprint)
    app.register_blueprint(appearance_blueprint)
    app.register_blueprint(media_blueprint)
    app.register_blueprint(music_blueprint)
    app.register_blueprint(system_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(extensions_blueprint)
    app.register_blueprint(providers_blueprint)
    register_provider_routes(app)
    return app
