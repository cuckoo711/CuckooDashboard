"""Flask application factory for Cuckoo Dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from flask import Flask
from flask_sock import Sock

from app.security import register_security_hooks
from core.config import DATA_DIR
from features.appearance.routes import blueprint as appearance_blueprint
from features.dashboard.routes import blueprint as dashboard_blueprint
from features.media.routes import blueprint as media_blueprint
from features.music.routes import blueprint as music_blueprint
from features.providers.routes import blueprint as providers_blueprint
from features.providers.routes import register_provider_routes
from features.settings.routes import blueprint as settings_blueprint
from features.settings.workspace_routes import blueprint as settings_workspaces_blueprint
from features.system.routes import blueprint as system_blueprint
from features.workspaces.routes import blueprint as workspaces_blueprint
from runtime.lifecycle import DashboardRuntime

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

    dashboard_runtime = runtime or DashboardRuntime(
        workspace_database=app.config["WORKSPACE_DATABASE"]
    )
    dashboard_runtime.init_app(app)

    sock = Sock(app)
    app.extensions["dashboard_sock"] = sock
    dashboard_runtime.websocket.register(sock)

    register_security_hooks(app)
    app.register_blueprint(dashboard_blueprint)
    app.register_blueprint(settings_blueprint)
    app.register_blueprint(settings_workspaces_blueprint)
    app.register_blueprint(appearance_blueprint)
    app.register_blueprint(media_blueprint)
    app.register_blueprint(music_blueprint)
    app.register_blueprint(system_blueprint)
    app.register_blueprint(workspaces_blueprint)
    app.register_blueprint(providers_blueprint)
    register_provider_routes(app)
    return app
