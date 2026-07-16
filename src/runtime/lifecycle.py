"""Top-level dashboard background runtime lifecycle."""

from __future__ import annotations

import threading

from providers.auth import AuthRefreshScheduler, refresh_scheduler
from runtime.websocket import WebSocketHub
from services.media_service import stop_media_service
from services.spectrum_service import shutdown_spectrum
from services.system_service import stop_system_service
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.registry import WorkspaceRegistry


class DashboardRuntime:
    """Own background components that must stop cleanly with the application."""

    def __init__(
        self,
        app=None,
        *,
        websocket: WebSocketHub | None = None,
        auth_scheduler: AuthRefreshScheduler | None = None,
        workspace_registry: WorkspaceRegistry | None = None,
    ) -> None:
        self.workspace_registry = (
            workspace_registry
            if workspace_registry is not None
            else create_builtin_workspace_registry()
        )
        self.websocket = (
            websocket
            if websocket is not None
            else WebSocketHub(workspace_registry=self.workspace_registry)
        )
        # Compatibility alias used by feature route modules.
        self.hub = self.websocket
        self.auth_scheduler = auth_scheduler or refresh_scheduler
        self._lock = threading.RLock()
        self._started = False
        if app is not None:
            self.init_app(app)

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    def init_app(self, app) -> "DashboardRuntime":
        app.extensions["dashboard_runtime"] = self
        app.extensions["workspace_registry"] = self.workspace_registry
        return self

    def start(self) -> bool:
        """Start the WebSocket hub and provider refresh scheduler idempotently."""
        with self._lock:
            websocket_started = self.websocket.start()
            self.auth_scheduler.start()
            changed = not self._started or websocket_started
            self._started = True
            return changed

    def stop(self, timeout: float = 5) -> None:
        """Stop all runtime workers and lazy services idempotently."""
        timeout = max(0.0, float(timeout))
        with self._lock:
            self.websocket.stop(timeout=timeout)
            self.auth_scheduler.stop(timeout=timeout)
            shutdown_spectrum(timeout=timeout)
            stop_media_service(timeout=timeout)
            stop_system_service(timeout=timeout)
            self._started = False

    def health(self) -> dict:
        return {
            "started": self._started,
            "websocket": self.websocket.health(),
            "auth": self.auth_scheduler.health(),
        }


def get_runtime(app=None) -> DashboardRuntime:
    """Return the runtime stored in ``Flask.app.extensions``."""
    if app is None:
        from flask import current_app

        app = current_app
    return app.extensions["dashboard_runtime"]
