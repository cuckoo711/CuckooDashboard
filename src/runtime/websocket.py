"""Dashboard WebSocket protocol facade over transport, subscriptions and realtime channels."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from contracts.subscription import (
    SourceSubscription,
    SubscriptionContractError,
    SubscriptionRequest,
)
from core.config import load_config, set_config_value
from devices.service import DeviceValidationError
from features.appearance.service import get_font_payload
from runtime.client_session import (
    ClientSession,
    ViewportContractError,
    normalize_viewport_payload,
)
from runtime.refresh_scheduler import RefreshScheduler
from runtime.source_cache import SourceCache
from runtime.subscription_broker import SubscriptionBroker
from runtime.websocket_transport import WebSocketTransport
from services.media_service import get_lyric_frame
from services.spectrum_service import (
    acquire_spectrum,
    get_spectrum_frame,
    load_music_offsets,
    release_spectrum,
)
from services.theme import load_theme_index, theme_response
from workspaces.builtins import create_builtin_workspace_registry

logger = logging.getLogger("cuckoo.runtime.websocket")

_LYRIC_POLL_INTERVAL_S = 0.12
_SPECTRUM_FPS_MIN = 12
_SPECTRUM_FPS_MAX = 60
_SPECTRUM_FPS_DEFAULT = 24
_SPECIAL_LYRIC_CHANNEL = "media.lyric"


def _load_vibe_state() -> bool:
    return bool(load_config().get("vibe_active", False))


def _save_vibe_state(active: bool) -> None:
    set_config_value("vibe_active", bool(active))


def _dashboard_media_payload(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Return the small media shape used by dashboard clients."""
    keep = {
        "status", "title", "artist", "album", "song_id",
        "lyric", "next_lyric", "lyric_index", "next_lyric_index",
        "position", "position_effective", "duration", "progress_ratio", "position_source",
        "lyric_offset", "lyric_start", "lyric_end", "lyric_duration", "lyric_elapsed",
        "lyric_scroll", "lyric_line_progress", "server_ts",
    }
    slim = {key: frame.get(key) for key in keep if key in frame}
    slim["media_slim"] = True
    slim["lyrics"] = []
    slim["lyrics_yrc"] = []
    return slim


def _clamp_spectrum_fps(value: Any) -> int:
    try:
        fps = int(round(float(value)))
    except (TypeError, ValueError):
        fps = _SPECTRUM_FPS_DEFAULT
    return max(_SPECTRUM_FPS_MIN, min(_SPECTRUM_FPS_MAX, fps))


class WebSocketHub:
    """Compatibility protocol facade; ordinary source work is delegated to the broker."""

    def __init__(
        self,
        workspace_registry: Any = None,
        *,
        source_cache: SourceCache | None = None,
        refresh_scheduler: RefreshScheduler | None = None,
        subscription_broker: SubscriptionBroker | None = None,
        transport: WebSocketTransport | None = None,
        is_owner_available=None,
        device_service: Any = None,
    ) -> None:
        self.workspace_registry = workspace_registry or create_builtin_workspace_registry()
        self.device_service = device_service
        if subscription_broker is not None:
            refresh_scheduler = refresh_scheduler or subscription_broker.scheduler
            source_cache = source_cache or subscription_broker.cache
        self.source_cache = source_cache or SourceCache()
        self.refresh_scheduler = refresh_scheduler or RefreshScheduler(
            self.workspace_registry,
            cache=self.source_cache,
        )
        self.subscription_broker = subscription_broker or SubscriptionBroker(
            self.workspace_registry,
            self.refresh_scheduler,
            is_owner_available=is_owner_available,
        )
        self._owns_scheduler = refresh_scheduler is None and subscription_broker is None

        self.transport = transport or WebSocketTransport()
        self.transport.on_open = self._on_open
        self.transport.on_message = self._on_message
        self.transport.on_close = self._on_close

        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._vibe = _load_vibe_state()
        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._lyric_last_push_key: tuple[str, int, str, str] | None = None
        self._started_at: float | None = None

    def register(self, sock: Any) -> None:
        """Register the ``/ws`` route or handle one accepted socket connection."""
        is_extension = callable(getattr(sock, "route", None)) and not callable(
            getattr(sock, "receive", None)
        )
        if not is_extension:
            self.start()
        self.transport.register(sock)

    def start(self) -> bool:
        """Start transport and the lyric/spectrum workers idempotently."""
        with self._lifecycle_lock:
            changed = False
            if self._owns_scheduler:
                changed = self.refresh_scheduler.start() or changed
            changed = self.transport.start() or changed
            if any(thread.is_alive() for thread in self._threads.values()):
                return changed
            self._threads = {}
            self._stop_event.clear()
            for name, target in {
                "spectrum": self._spectrum_loop,
                "lyric": self._lyric_loop,
            }.items():
                thread = threading.Thread(target=target, daemon=True, name=f"ws-{name}")
                self._threads[name] = thread
                thread.start()
            self._started_at = time.time()
            self._apply_vibe_refresh_policy(self._vibe)
            return True

    def stop(self, timeout: float = 5) -> None:
        """Close sessions and stop only the realtime workers owned by this facade."""
        timeout = max(0.0, float(timeout))
        with self._lifecycle_lock:
            self._stop_event.set()
            self.transport.stop(timeout=timeout)
            deadline = time.monotonic() + timeout
            for thread in tuple(self._threads.values()):
                if thread is threading.current_thread():
                    continue
                thread.join(max(0.0, deadline - time.monotonic()))
            self._threads = {
                name: thread for name, thread in self._threads.items() if thread.is_alive()
            }
            if self._owns_scheduler:
                self.refresh_scheduler.stop(timeout=timeout)
            if not self._threads:
                self._started_at = None

    def health(self) -> dict[str, Any]:
        transport_health = self.transport.health()
        sessions = self.transport.sessions()
        with self._lifecycle_lock:
            threads = {name: thread.is_alive() for name, thread in self._threads.items()}
            realtime_running = bool(threads) and all(threads.values()) and not self._stop_event.is_set()
        running = bool(transport_health.get("running")) and realtime_running
        return {
            "status": "ok" if running else "stopped",
            "ok": running,
            "running": running,
            "clients": len(sessions),
            "spectrum_clients": sum(1 for session in sessions if session.spectrum),
            "lyric_clients": sum(1 for session in sessions if self._state_wants_lyric(session)),
            "threads": threads,
            "executor": False,
            "started_at": self._started_at,
            "transport": transport_health,
        }

    def broadcast(self, msg: dict[str, Any]) -> None:
        message_type = str(msg.get("type") or "")
        source_id = self._source_id_for_message_type(message_type)
        if source_id is not None and "data" in msg:
            self.subscription_broker.publish_external(source_id, msg.get("data"))
            return
        self.transport.broadcast(msg)

    def broadcast_media(self, frame: dict, source_id: str | None = None) -> None:
        resolved = source_id or self._source_id_for_message_type("media") or "media.playback"
        self.subscription_broker.publish_external(resolved, frame)

    def broadcast_lyric(self, msg: dict) -> int:
        return self.transport.broadcast(msg, predicate=self._state_wants_lyric)

    def broadcast_settings_update(self) -> None:
        for source_id in self._known_source_ids():
            try:
                self.subscription_broker.invalidate(source_id, refresh=True)
            except Exception:
                continue
        self.broadcast({"type": "config_updated", "data": {"ok": True}})
        try:
            self.broadcast({"type": "theme", "data": theme_response(load_theme_index())})
        except Exception:
            pass
        try:
            self.broadcast({"type": "font", "data": get_font_payload()})
        except Exception:
            pass
        self.set_vibe(_load_vibe_state())

    def list_clients(self) -> list[dict[str, Any]]:
        # Settings is a management surface, not a display terminal. Keep it out of
        # the online-board / online-device listings so operators only see real dashboards.
        return [
            session.list_payload()
            for session in self.transport.sessions()
            if str(session.page or "").strip().lower() != "settings"
        ]

    def navigate_client(
        self,
        client_id: str,
        page: str = "dashboard",
        *,
        workspace_id: str | None = None,
        url: str | None = None,
    ) -> bool:
        if workspace_id is not None:
            workspace_id = str(workspace_id).strip()
            if not workspace_id:
                return False
            page = "dashboard"
            url = "/" if workspace_id == "main" else f"/workspaces/{quote(workspace_id, safe='')}"
        elif page not in {"dashboard", "music"}:
            return False
        elif url is None:
            url = "/" if page == "dashboard" else "/music"
        message: dict[str, Any] = {"type": "navigate", "page": page, "url": url}
        if page == "dashboard":
            message["workspace_id"] = workspace_id or "main"
        return self.transport.send_to(client_id, message)

    def workspace_client_ids(self, workspace_id: str) -> list[str]:
        workspace_id = str(workspace_id or "")
        return [
            session.client_id
            for session in self.transport.sessions()
            if session.page == "dashboard" and (session.workspace_id or "main") == workspace_id
        ]

    def request_screenshot(self, client_id: str) -> str | None:
        if self.transport.get_session(client_id) is None:
            return None
        request_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        if not self.transport.send_to(client_id, {"type": "screenshot", "request_id": request_id}):
            return None
        return request_id

    def get_vibe(self) -> bool:
        with self._lock:
            return bool(self._vibe)

    def set_vibe(self, active: Any) -> bool:
        active = bool(active)
        _save_vibe_state(active)
        with self._lock:
            self._vibe = active
            for session in self.transport.sessions():
                session.vibe = active
        self._apply_vibe_refresh_policy(active)
        self.transport.broadcast({"type": "vibe_state", "data": {"active": active}})
        return active

    def force_lyric_sync(self) -> None:
        try:
            frame = get_lyric_frame()
            with self._lock:
                self._lyric_last_push_key = self._lyric_key(frame)
            self.broadcast_lyric({"type": "lyric", "data": frame})
        except Exception as exc:
            logger.debug("[ws] forced lyric sync failed: %s", exc)

    def _on_open(self, session: ClientSession) -> None:
        saved_vibe = _load_vibe_state()
        session.vibe = saved_vibe
        self.subscription_broker.register_session(
            session,
            send=lambda payload: self.transport.send(session, payload),
            page=session.page,
            workspace_id=session.workspace_id,
            legacy_all=False,
        )
        with self._lock:
            self._recalc_vibe_locked()
        logger.info(
            "[ws] client connected (total: %s, id: %s)",
            len(self.transport.sessions()),
            session.client_id,
        )
        self._send(session, {"type": "connected", "id": session.client_id})

    def _on_close(self, session: ClientSession) -> None:
        self.subscription_broker.close_session(session)
        had_spectrum = session.spectrum
        session.spectrum = False
        if had_spectrum:
            release_spectrum()
        with self._lock:
            self._recalc_vibe_locked()
        logger.info("[ws] client disconnected (total: %s)", len(self.transport.sessions()))

    def _bind_device(self, session: ClientSession, msg: Mapping[str, Any]) -> bool:
        """Attach a persistent browser device id to the live WS session."""
        raw_device_id = msg.get("device_id")
        if raw_device_id in (None, ""):
            return True
        service = self.device_service
        if service is None:
            return True
        try:
            device = service.register({
                "device_id": raw_device_id,
                "display_name": msg.get("display_name") or "",
                "page": msg.get("page") or session.page or "",
                "viewport": msg.get("viewport") if isinstance(msg.get("viewport"), Mapping) else None,
            })
        except DeviceValidationError as exc:
            self._send_protocol_error(session, "invalid_device", str(exc))
            return False
        session.device_id = str(device.get("id") or "")
        session.device_status = str(device.get("status") or "")
        if device.get("status") != "approved":
            self._send(
                session,
                {
                    "type": "device_status",
                    "data": service.session_payload(device),
                },
            )
            return False
        assigned = str(device.get("workspace_id") or "main")
        # Preserve the page-reported workspace/page selection for music/settings,
        # but bind dashboard sessions onto the approved workspace assignment.
        if session.page in {None, "", "unknown", "dashboard"} and str(msg.get("page") or "") in {"", "dashboard"}:
            session.workspace_id = assigned
        return True

    def _on_message(self, session: ClientSession, msg: dict[str, Any]) -> None:
        try:
            msg_type = msg.get("type")
            # Always attempt to attach device identity before page bookkeeping so
            # music/settings clients remain visible as online terminals.
            if msg_type in {"report", "init", "subscribe"}:
                bound = self._bind_device(session, msg)
                if not bound and msg_type != "report":
                    return
            if msg_type == "vibe":
                if session.device_status and session.device_status != "approved":
                    return
                session.vibe = bool(msg.get("active"))
                with self._lock:
                    vibe = self._recalc_vibe_locked()
                _save_vibe_state(vibe)
                self._apply_vibe_refresh_policy(vibe)
                logger.info("[ws] vibe coding: %s", "ON" if vibe else "OFF")
            elif msg_type == "report":
                page = str(msg.get("page") or "unknown").strip() or "unknown"
                workspace_id = None
                viewport = None
                if page == "dashboard":
                    try:
                        viewport = normalize_viewport_payload(
                            msg.get("viewport"),
                            require_workspace=True,
                        )
                    except ViewportContractError as exc:
                        self._send_protocol_error(session, "invalid_viewport", str(exc))
                        return
                    if session.device_status == "approved" and session.workspace_id:
                        workspace_id = session.workspace_id
                    else:
                        workspace_id = str(msg.get("workspace_id") or "main").strip() or "main"
                elif isinstance(msg.get("viewport"), Mapping):
                    try:
                        viewport = normalize_viewport_payload(
                            msg.get("viewport"),
                            require_workspace=False,
                        )
                    except ViewportContractError as exc:
                        self._send_protocol_error(session, "invalid_viewport", str(exc))
                        return
                session.page = page
                session.workspace_id = workspace_id if page == "dashboard" else None
                if viewport is not None:
                    session.set_viewport(viewport)
                # Keep the last known viewport for music/settings pages so
                # Settings calibration can still use the display size.
                # Only clear when the client explicitly reports an invalid/empty
                # geometry object is not present *and* never reported before.
                elif page == "dashboard":
                    session.clear_viewport()
                # Presence should still work for music/settings even before/without
                # dashboard subscription approval gating; only data/subscribe needs approval.
                if session.device_status in {None, "", "approved"}:
                    self.subscription_broker.report_session(
                        session,
                        page=page,
                        workspace_id=workspace_id,
                    )
                # Lyric interest remains page-driven for legacy clients that have
                # not yet attached a device_id (local/dev sockets, unit tests).
                if session.device_status not in {"pending", "disabled"} and (
                    page == "music"
                    or (
                        page == "dashboard"
                        and session.wire_mode == "legacy"
                        and session.source_subscriptions is None
                    )
                ):
                    session.lyric = True
                logger.info(
                    "[ws] client %s reports page: %s%s%s device=%s",
                    session.client_id,
                    page,
                    f" ({workspace_id})" if workspace_id else "",
                    (
                        f" [{viewport['workspace_width']}x{viewport['workspace_height']} CSS px]"
                        if viewport is not None
                        else ""
                    ),
                    session.device_id or "-",
                )
            elif msg_type == "subscribe":
                if session.device_status and session.device_status != "approved":
                    self._send_protocol_error(session, "device_pending", "终端尚未审批")
                    return
                channel = str(msg.get("channel") or "")
                if channel == "spectrum":
                    active = bool(msg.get("active"))
                    self._set_spectrum_interest(session, active, msg.get("fps"))
                    if active and self._send(
                        session,
                        {"type": "spectrum", "data": get_spectrum_frame()},
                    ):
                        self._send(session, {"type": "music_offset", "data": load_music_offsets()})
                        session.spectrum_last_sent_at = time.monotonic()
                elif channel == "lyric":
                    self._set_lyric_interest(session, bool(msg.get("active")), explicit=True)
                elif "subscriptions" in msg:
                    self._replace_card_subscriptions(session, msg)
                elif "sources" in msg:
                    self._subscribe_sources(
                        session,
                        msg.get("sources"),
                        replace=bool(msg.get("replace", True)),
                    )
            elif msg_type == "unsubscribe":
                if "subscriptions" in msg or "subscription_ids" in msg or "ids" in msg:
                    self._unsubscribe_card_subscriptions(session, msg)
                elif "sources" in msg:
                    self._unsubscribe_sources(session, msg.get("sources"))
            elif msg_type == "init":
                if session.device_status and session.device_status != "approved":
                    self._send_protocol_error(session, "device_pending", "终端尚未审批")
                    return
                self._send_all_data(session)
            elif msg_type == "ping":
                self._send(session, {"type": "pong", "ts": msg.get("ts")})
            elif msg_type == "screenshot_data":
                self.broadcast({
                    "type": "screenshot_result",
                    "request_id": msg.get("request_id"),
                    "client_id": session.client_id,
                    "data": msg.get("data"),
                    "timestamp": time.time(),
                })
                logger.info("[ws] screenshot received from %s", session.client_id)
        except (KeyError, TypeError, ValueError):
            return

    def _replace_card_subscriptions(
        self,
        session: ClientSession,
        payload: Mapping[str, Any],
    ) -> None:
        raw = payload.get("subscriptions")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            self._send_source_error(session, "invalid_subscriptions", "subscriptions must be an array")
            return
        replace = bool(payload.get("replace", True))
        current = {} if replace else dict(session.subscriptions)
        incoming: dict[str, dict[str, Any]] = {}
        ordinary: list[Mapping[str, Any]] = []
        seen_ids: set[str] = set()
        try:
            for index, item in enumerate(raw):
                if not isinstance(item, Mapping):
                    raise SubscriptionContractError(
                        "invalid_subscription",
                        "subscription must be an object",
                    )
                subscription_id = str(
                    item.get("id")
                    or item.get("subscription_id")
                    or item.get("subscriptionId")
                    or f"subscription:{index}"
                )
                if subscription_id in seen_ids:
                    raise SubscriptionContractError(
                        "duplicate_subscription_id",
                        f"duplicate subscription id: {subscription_id}",
                        subscription_id=subscription_id,
                    )
                seen_ids.add(subscription_id)
                channel = str(
                    item.get("channel")
                    or item.get("source_id")
                    or item.get("source")
                    or ""
                )
                normalized = dict(item)
                normalized["id"] = subscription_id
                normalized["channel"] = channel
                incoming[subscription_id] = normalized
                if channel != _SPECIAL_LYRIC_CHANNEL:
                    ordinary.append(normalized)
            request = SubscriptionRequest.from_payload(
                {"subscriptions": ordinary, "replace": replace}
            )
            self.subscription_broker.replace_subscriptions(
                session,
                request.subscriptions,
                replace=replace,
                replay=False,
            )
        except SubscriptionContractError as exc:
            self._send(session, {"type": "source_error", "error": exc.to_error().to_payload()})
            return
        target = current
        target.update(incoming)
        session.subscriptions = target
        session.wire_mode = "snapshot"
        session.source_subscriptions = {
            item.source_id for item in self.subscription_broker.subscriptions_for_session(session)
        }
        wants_lyric = any(
            str(item.get("channel") or "") == _SPECIAL_LYRIC_CHANNEL
            for item in target.values()
        )
        self._set_lyric_interest(session, wants_lyric, explicit=True)

    def _unsubscribe_card_subscriptions(
        self,
        session: ClientSession,
        payload: Mapping[str, Any],
    ) -> None:
        ids = payload.get("subscription_ids", payload.get("ids", ()))
        if isinstance(ids, str):
            ids = (ids,)
        raw = payload.get("subscriptions", ())
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            ids = tuple(ids) + tuple(
                str(item.get("id") or item.get("subscriptionId") or "")
                for item in raw
                if isinstance(item, Mapping)
            )
        selected = {str(value) for value in ids if value is not None}
        self.subscription_broker.unsubscribe(session, subscription_ids=tuple(selected))
        session.subscriptions = {
            key: value for key, value in session.subscriptions.items() if key not in selected
        }
        session.source_subscriptions = {
            item.source_id for item in self.subscription_broker.subscriptions_for_session(session)
        }
        wants_lyric = any(
            str(item.get("channel") or "") == _SPECIAL_LYRIC_CHANNEL
            for item in session.subscriptions.values()
        )
        self._set_lyric_interest(session, wants_lyric, explicit=True)

    def _subscribe_sources(self, session: ClientSession, sources: Any, replace: bool) -> None:
        requested = self._normalize_source_ids(sources)
        known = self._known_source_ids()
        selected = {source_id for source_id in requested if source_id in known}
        current = session.source_subscriptions
        target = selected if replace else (set() if current is None else set(current)) | selected
        subscriptions = tuple(
            SourceSubscription(id=f"legacy:{source_id}", source_id=source_id)
            for source_id in self._ordered_source_ids(target)
        )
        self.subscription_broker.replace_subscriptions(
            session,
            subscriptions,
            replace=True,
            replay=False,
        )
        session.wire_mode = "legacy"
        session.source_subscriptions = set(target)

    def _unsubscribe_sources(self, session: ClientSession, sources: Any) -> None:
        known = self._known_source_ids()
        selected = set(self._normalize_source_ids(sources)) & known
        if not selected:
            return
        current = set(known) if session.source_subscriptions is None else set(
            session.source_subscriptions
        )
        current.difference_update(selected)
        self._subscribe_sources(session, current, replace=True)

    def _ensure_legacy_all(self, session: ClientSession) -> None:
        if session.wire_mode != "legacy" or session.source_subscriptions is not None:
            return
        subscriptions = tuple(
            SourceSubscription(id=f"legacy:{source_id}", source_id=source_id)
            for source_id in self._ordered_source_ids(self._known_source_ids())
        )
        self.subscription_broker.replace_subscriptions(
            session,
            subscriptions,
            replace=True,
            replay=False,
        )

    def _send_all_data(self, target: Any) -> None:
        session = self._resolve_session(target)
        if session is None:
            return
        self._send(session, {"type": "vibe_state", "data": {"active": _load_vibe_state()}})
        self._ensure_legacy_all(session)
        self.subscription_broker.init_session(
            session,
            refresh_missing=True,
            wait_for_refresh=True,
        )
        try:
            self._send(session, {"type": "theme", "data": theme_response(load_theme_index())})
        except Exception:
            pass
        try:
            self._send(session, {"type": "font", "data": get_font_payload()})
        except Exception:
            pass

    def _handle_message(self, target: Any, client_id: str, raw: Any) -> None:
        del client_id
        session = self._resolve_session(target)
        if session is None:
            return
        try:
            message = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return
        if isinstance(message, dict):
            self._on_message(session, message)

    def _broadcast_due_sources(self, now: float | None = None, executor: Any = None) -> None:
        del executor
        self.refresh_scheduler.run_due(now=now, wait=True)

    def _spectrum_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            target_fps = 0
            try:
                target_fps = self._spectrum_target_fps()
                if target_fps:
                    self._broadcast_spectrum(
                        {"type": "spectrum", "data": get_spectrum_frame()},
                        now=started,
                    )
            except Exception as exc:
                logger.error("[ws] spectrum broadcast error: %s", exc)
            elapsed = time.monotonic() - started
            interval = 1.0 / target_fps if target_fps else 0.25
            self._stop_event.wait(max(0.0, interval - elapsed))

    def _lyric_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                if self._lyric_interest_count() > 0:
                    frame = get_lyric_frame()
                    key = self._lyric_key(frame)
                    with self._lock:
                        changed = key != self._lyric_last_push_key
                        if changed:
                            self._lyric_last_push_key = key
                    if changed:
                        self.broadcast_lyric({"type": "lyric", "data": frame})
            except Exception as exc:
                logger.error("[ws] lyric broadcast error: %s", exc)
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.0, _LYRIC_POLL_INTERVAL_S - elapsed))

    def _broadcast_spectrum(self, msg: dict, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        sent = 0
        for session in self.transport.sessions():
            if not session.spectrum:
                continue
            fps = _clamp_spectrum_fps(session.spectrum_fps)
            if now - session.spectrum_last_sent_at + 1e-6 < 1.0 / fps:
                continue
            session.spectrum_last_sent_at = now
            if self._send(session, msg):
                sent += 1
        return sent

    def _set_spectrum_interest(
        self,
        session: ClientSession,
        active: bool,
        fps: Any = None,
    ) -> None:
        was_active = bool(session.spectrum)
        if active:
            session.spectrum_fps = _clamp_spectrum_fps(
                fps if fps is not None else session.spectrum_fps
            )
            session.spectrum_last_sent_at = 0.0
        session.spectrum = bool(active)
        if was_active == bool(active):
            return
        if active:
            acquire_spectrum()
            logger.info("[ws] spectrum subscribe ON (%sfps)", session.spectrum_fps)
        else:
            release_spectrum()
            logger.info("[ws] spectrum subscribe OFF")

    def _set_lyric_interest(
        self,
        session: ClientSession,
        active: bool,
        *,
        explicit: bool,
    ) -> None:
        session.lyric = bool(active)
        if explicit:
            session.lyric_explicit = True
        if active:
            self._send(session, {"type": "lyric", "data": get_lyric_frame()})

    def _recalc_vibe_locked(self) -> bool:
        sessions = self.transport.sessions()
        if sessions:
            self._vibe = any(bool(session.vibe) for session in sessions)
        else:
            self._vibe = _load_vibe_state()
        return self._vibe

    def _apply_vibe_refresh_policy(self, active: bool) -> None:
        for source_id in self._known_source_ids():
            try:
                self.refresh_scheduler.set_source_active(source_id, bool(active))
            except Exception:
                continue

    def _source_id_for_message_type(self, message_type: str) -> str | None:
        for definition in self.workspace_registry.iter_data_sources():
            if definition.descriptor.legacy_message_type == message_type:
                return definition.descriptor.id
        return None

    def _known_source_ids(self) -> set[str]:
        return set(self.workspace_registry.data_source_ids())

    def _ordered_source_ids(self, source_ids: set[str]) -> tuple[str, ...]:
        order = {"dashboard_data": 0, "github": 1, "media": 2, "system": 3}
        definitions = [
            self.workspace_registry.get_data_source(source_id)
            for source_id in source_ids
        ]
        return tuple(
            definition.descriptor.id
            for definition in sorted(
                definitions,
                key=lambda item: (
                    order.get(item.descriptor.legacy_message_type, len(order)),
                    item.descriptor.id,
                ),
            )
        )

    @staticmethod
    def _normalize_source_ids(sources: Any) -> list[str]:
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, (list, tuple, set)):
            return []
        return [str(source_id) for source_id in sources if source_id is not None]

    def _resolve_session(self, target: Any) -> ClientSession | None:
        if isinstance(target, ClientSession):
            return target if self.transport.get_session(target.client_id) is target else None
        if isinstance(target, str):
            return self.transport.get_session(target)
        for session in self.transport.sessions():
            if session.socket is target:
                return session
        return None

    def _send(self, target: Any, msg: dict[str, Any]) -> bool:
        return self.transport.send(target, msg)

    def _send_source_error(
        self,
        session: ClientSession,
        code: str,
        message: str,
    ) -> None:
        self._send(
            session,
            {
                "type": "source_error",
                "error": {"code": code, "message": message, "retryable": False},
            },
        )

    def _send_protocol_error(
        self,
        session: ClientSession,
        code: str,
        message: str,
    ) -> None:
        self._send(
            session,
            {
                "type": "protocol_error",
                "error": {"code": code, "message": message, "retryable": False},
            },
        )

    def _spectrum_target_fps(self) -> int:
        return max(
            (
                _clamp_spectrum_fps(session.spectrum_fps)
                for session in self.transport.sessions()
                if session.spectrum
            ),
            default=0,
        )

    def _lyric_interest_count(self) -> int:
        return sum(
            1 for session in self.transport.sessions() if self._state_wants_lyric(session)
        )

    @staticmethod
    def _state_wants_lyric(session: ClientSession) -> bool:
        if session.lyric_explicit:
            return bool(session.lyric)
        if session.source_subscriptions is not None or session.wire_mode == "snapshot":
            return bool(session.lyric)
        return bool(session.lyric) or session.page in {"music", "dashboard"}

    @staticmethod
    def _lyric_key(frame: dict) -> tuple[str, int, str, str]:
        return (
            str(frame.get("track_key") or ""),
            int(frame.get("lyric_index", -1) if frame.get("lyric_index") is not None else -1),
            str(frame.get("status") or ""),
            str(frame.get("lyric") or ""),
        )


__all__ = ["WebSocketHub", "_clamp_spectrum_fps", "_dashboard_media_payload"]
