"""Top-level dashboard background runtime lifecycle."""

from __future__ import annotations

import threading

from core.config import DATA_DIR
from devices.repository import DeviceRepository
from devices.service import DeviceService
from providers.auth import AuthRefreshScheduler, refresh_scheduler as provider_refresh_scheduler
from runtime.refresh_scheduler import RefreshScheduler
from runtime.source_cache import SourceCache
from runtime.subscription_broker import SubscriptionBroker
from runtime.websocket import WebSocketHub
from runtime.websocket_transport import WebSocketTransport
from services.media_service import stop_media_service
from services.spectrum_service import shutdown_spectrum
from services.system_service import stop_system_service
from workspaces.builtins import create_builtin_workspace_registry
from workspaces.registry import WorkspaceRegistry
from workspaces.repository import WorkspaceRepository
from workspaces.service import WorkspaceService


class DashboardRuntime:
    """Own background components that must stop cleanly with the application."""

    def __init__(
        self,
        app=None,
        *,
        websocket: WebSocketHub | None = None,
        auth_scheduler: AuthRefreshScheduler | None = None,
        workspace_registry: WorkspaceRegistry | None = None,
        workspace_repository: WorkspaceRepository | None = None,
        workspace_service: WorkspaceService | None = None,
        workspace_database: str | None = None,
        device_repository: DeviceRepository | None = None,
        device_service: DeviceService | None = None,
        device_database: str | None = None,
        extension_manager=None,
        source_cache: SourceCache | None = None,
        refresh_scheduler: RefreshScheduler | None = None,
        subscription_broker: SubscriptionBroker | None = None,
        websocket_transport: WebSocketTransport | None = None,
    ) -> None:
        self.workspace_registry = (
            workspace_registry
            if workspace_registry is not None
            else create_builtin_workspace_registry()
        )
        self.extension_manager = extension_manager

        if websocket is None:
            self.source_cache = source_cache or SourceCache()
            self.refresh_scheduler = refresh_scheduler or RefreshScheduler(
                self.workspace_registry,
                cache=self.source_cache,
            )
            self.subscription_broker = subscription_broker or SubscriptionBroker(
                self.workspace_registry,
                self.refresh_scheduler,
                is_owner_available=(
                    self.extension_manager.is_owner_available
                    if self.extension_manager is not None
                    else None
                ),
            )
            self.websocket_transport = websocket_transport or WebSocketTransport()
            self.device_repository = device_repository or DeviceRepository(
                device_database or str(DATA_DIR / "devices.db")
            )
            self.device_service = device_service or DeviceService(
                self.device_repository,
                workspace_exists=self._workspace_exists,
            )
            self.websocket = WebSocketHub(
                workspace_registry=self.workspace_registry,
                source_cache=self.source_cache,
                refresh_scheduler=self.refresh_scheduler,
                subscription_broker=self.subscription_broker,
                transport=self.websocket_transport,
                is_owner_available=(
                    self.extension_manager.is_owner_available
                    if self.extension_manager is not None
                    else None
                ),
                device_service=self.device_service,
            )
        else:
            # Preserve the historical injected-websocket contract: do not attach
            # runtime attributes to arbitrary fakes or externally-owned objects.
            self.websocket = websocket
            self.source_cache = source_cache or getattr(websocket, "source_cache", None)
            self.refresh_scheduler = refresh_scheduler or getattr(
                websocket, "refresh_scheduler", None
            )
            self.subscription_broker = subscription_broker or getattr(
                websocket, "subscription_broker", None
            )
            self.websocket_transport = websocket_transport or getattr(
                websocket, "transport", None
            )

        # Compatibility alias used by feature route modules.
        self.hub = self.websocket
        if workspace_service is not None:
            self.workspace_service = workspace_service
            self.workspace_repository = workspace_service.repository
        else:
            self.workspace_repository = workspace_repository or WorkspaceRepository(
                workspace_database or str(DATA_DIR / "workspaces.db")
            )
            try:
                seed_workspace = self.workspace_registry.get_workspace("main")
            except KeyError:
                seed_workspace = None
            self.workspace_service = WorkspaceService(
                self.workspace_repository,
                self.workspace_registry,
                seed_workspace=seed_workspace,
                is_workspace_in_use=self._workspace_in_use,
                is_owner_available=(
                    self.extension_manager.is_owner_available
                    if self.extension_manager is not None
                    else None
                ),
                owner_allows_new_widgets=(
                    self.extension_manager.owner_allows_new_widgets
                    if self.extension_manager is not None
                    else None
                ),
                owner_unavailable_reason=(
                    self.extension_manager.owner_unavailable_reason
                    if self.extension_manager is not None
                    else None
                ),
            )
        if not hasattr(self, "device_repository"):
            self.device_repository = device_repository or DeviceRepository(
                device_database or str(DATA_DIR / "devices.db")
            )
        if not hasattr(self, "device_service") or self.device_service is None:
            self.device_service = device_service or DeviceService(
                self.device_repository,
                workspace_exists=self._workspace_exists,
            )
        if getattr(self.websocket, "device_service", None) is None:
            try:
                self.websocket.device_service = self.device_service
            except Exception:
                pass
        self.auth_scheduler = auth_scheduler or provider_refresh_scheduler
        self._lock = threading.RLock()
        self._started = False
        if app is not None:
            self.init_app(app)

    def _workspace_exists(self, workspace_id: str) -> bool:
        try:
            self.workspace_service.get_workspace(str(workspace_id))
            return True
        except Exception:
            return False

    def _workspace_in_use(self, workspace_id: str) -> bool:
        list_clients = getattr(self.websocket, "list_clients", None)
        if not callable(list_clients):
            return False
        try:
            return any(
                client.get("workspace_id") == workspace_id
                for client in list_clients()
                if isinstance(client, dict)
            )
        except Exception:
            return False

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    def init_app(self, app) -> "DashboardRuntime":
        app.extensions["dashboard_runtime"] = self
        app.extensions["workspace_registry"] = self.workspace_registry
        app.extensions["workspace_repository"] = self.workspace_repository
        app.extensions["workspace_service"] = self.workspace_service
        app.extensions["device_repository"] = self.device_repository
        app.extensions["device_service"] = self.device_service
        if self.source_cache is not None:
            app.extensions["source_cache"] = self.source_cache
        if self.refresh_scheduler is not None:
            app.extensions["refresh_scheduler"] = self.refresh_scheduler
        if self.subscription_broker is not None:
            app.extensions["subscription_broker"] = self.subscription_broker
        if self.websocket_transport is not None:
            app.extensions["websocket_transport"] = self.websocket_transport
        if self.extension_manager is not None:
            app.extensions["extension_manager"] = self.extension_manager
            app.extensions["extension_state_repository"] = (
                self.extension_manager.state_repository
            )
        return self

    def start(self) -> bool:
        """Start extensions, ordinary source scheduling, WebSocket and auth workers."""
        with self._lock:
            if self.extension_manager is not None:
                self.extension_manager.start_all(self)
            refresh_started = False
            if self.refresh_scheduler is not None:
                refresh_started = bool(self.refresh_scheduler.start())
            websocket_started = bool(self.websocket.start())
            self.auth_scheduler.start()
            changed = not self._started or refresh_started or websocket_started
            self._started = True
            return changed

    def stop(self, timeout: float = 5) -> None:
        """Stop transports before data getters, then stop extensions and lazy services."""
        timeout = max(0.0, float(timeout))
        with self._lock:
            self.websocket.stop(timeout=timeout)
            if self.refresh_scheduler is not None:
                self.refresh_scheduler.stop(timeout=timeout)
            self.auth_scheduler.stop(timeout=timeout)
            if self.extension_manager is not None:
                self.extension_manager.stop_all(self, timeout=timeout)
            shutdown_spectrum(timeout=timeout)
            stop_media_service(timeout=timeout)
            stop_system_service(timeout=timeout)
            self.workspace_service.close()
            self.device_repository.close()
            if self.extension_manager is not None:
                self.extension_manager.state_repository.close()
            self._started = False

    def health(self) -> dict:
        payload = {
            "started": self._started,
            "websocket": self.websocket.health(),
            "auth": self.auth_scheduler.health(),
        }
        if self.refresh_scheduler is not None:
            payload["refresh"] = self.refresh_scheduler.health()
        if self.subscription_broker is not None:
            payload["subscriptions"] = self.subscription_broker.health()
        if self.source_cache is not None:
            payload["source_cache"] = self.source_cache.health()
        if self.websocket_transport is not None:
            payload["transport"] = self.websocket_transport.health()
        if self.extension_manager is not None:
            payload["extensions"] = self.extension_manager.health()
        return payload


def get_runtime(app=None) -> DashboardRuntime:
    """Return the runtime stored in ``Flask.app.extensions``."""
    if app is None:
        from flask import current_app

        app = current_app
    return app.extensions["dashboard_runtime"]
