"""Pure WebSocket transport and connection lifecycle management."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from runtime.client_session import ClientSession

logger = logging.getLogger("cuckoo.runtime.websocket_transport")

MessageCallback = Callable[[ClientSession, dict[str, Any]], None]
SessionCallback = Callable[[ClientSession], None]


class WebSocketTransport:
    """Manage accepted sockets while delegating all business messages to callbacks."""

    def __init__(
        self,
        *,
        on_message: MessageCallback | None = None,
        on_open: SessionCallback | None = None,
        on_close: SessionCallback | None = None,
        path: str = "/ws",
        client_id_factory: Callable[..., str] | None = None,
    ) -> None:
        self.on_message = on_message
        self.on_open = on_open
        self.on_close = on_close
        self.path = path
        self._client_id_factory = client_id_factory
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._sessions_by_socket: dict[Any, ClientSession] = {}
        self._sessions_by_id: dict[str, ClientSession] = {}
        self._running = False
        self._started_at: float | None = None
        self._generation = 0

    def register(self, sock: Any) -> None:
        """Register the Flask-Sock route or run one accepted socket receive loop."""
        if callable(getattr(sock, "route", None)) and not callable(getattr(sock, "receive", None)):
            sock.route(self.path)(self.register)
            return
        self._serve_socket(sock)

    def start(self) -> bool:
        """Mark the transport running; no business worker threads are created."""
        with self._lifecycle_lock:
            if self._running:
                return False
            self._generation += 1
            self._running = True
            self._started_at = time.time()
            return True

    def stop(self, timeout: float = 5) -> None:
        """Stop accepting receive-loop work and close every current session."""
        del timeout  # Kept for lifecycle API compatibility; there are no workers to join.
        with self._lifecycle_lock:
            self._running = False
            self._generation += 1
            self._started_at = None
        for session in self.sessions():
            self._remove_session(session, close_socket=True)

    def restart(self, timeout: float = 5) -> bool:
        self.stop(timeout=timeout)
        return self.start()

    def health(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            running = self._running
            started_at = self._started_at
        with self._lock:
            clients = len(self._sessions_by_id)
        return {
            "status": "ok" if running else "stopped",
            "ok": running,
            "running": running,
            "clients": clients,
            "threads": {},
            "executor": False,
            "started_at": started_at,
        }

    def sessions(self) -> list[ClientSession]:
        with self._lock:
            return list(self._sessions_by_id.values())

    def list_sessions(self) -> list[dict[str, Any]]:
        return [session.list_payload() for session in self.sessions()]

    def list_clients(self) -> list[dict[str, Any]]:
        """Compatibility alias for callers migrating from ``WebSocketHub``."""
        return self.list_sessions()

    def get_session(self, client_id: str) -> ClientSession | None:
        with self._lock:
            return self._sessions_by_id.get(str(client_id))

    def send(self, target: Any, message: Any) -> bool:
        """Send to a session, client id, or registered socket."""
        session = self._resolve_session(target)
        if session is None:
            return False
        try:
            sent = session.send_json(message)
        except (TypeError, ValueError):
            return False
        if sent:
            return True
        self._remove_session(session, close_socket=True)
        return False

    def send_json(self, target: Any, message: Any) -> bool:
        return self.send(target, message)

    def send_to(self, client_id: str, message: Any) -> bool:
        return self.send(client_id, message)

    def target_send(self, client_id: str, message: Any) -> bool:
        """Explicitly named alias useful at composition call sites."""
        return self.send_to(client_id, message)

    def broadcast(
        self,
        message: Any,
        predicate: Callable[[ClientSession], bool] | None = None,
    ) -> int:
        sent = 0
        for session in self.sessions():
            if predicate is not None:
                try:
                    if not predicate(session):
                        continue
                except Exception:
                    continue
            if self.send(session, message):
                sent += 1
        return sent

    def remove(self, target: Any, *, close_socket: bool = True) -> bool:
        session = self._resolve_session(target)
        if session is None:
            return False
        return self._remove_session(session, close_socket=close_socket)

    def _serve_socket(self, sock: Any) -> None:
        self.start()
        with self._lifecycle_lock:
            generation = self._generation
        session = ClientSession(socket=sock, client_id=self._new_client_id(sock))
        with self._lock:
            self._sessions_by_socket[sock] = session
            self._sessions_by_id[session.client_id] = session

        self._invoke_session_callback(self.on_open, session, "open")
        try:
            while self._should_receive(session, generation):
                try:
                    raw = sock.receive(timeout=30)
                except TimeoutError:
                    continue
                except Exception:
                    break
                if raw is None:
                    if not bool(getattr(sock, "connected", True)):
                        break
                    continue
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                callback = self.on_message
                if callback is None:
                    continue
                try:
                    callback(session, message)
                except Exception:
                    logger.exception("WebSocket message callback failed for %s", session.client_id)
        finally:
            self._remove_session(session, close_socket=True)

    def _should_receive(self, session: ClientSession, generation: int) -> bool:
        with self._lifecycle_lock:
            running = self._running and generation == self._generation
        return running and not session.closed and bool(getattr(session.socket, "connected", True))

    def _new_client_id(self, sock: Any) -> str:
        factory = self._client_id_factory
        if factory is None:
            candidate = secrets.token_hex(4)
        else:
            try:
                candidate = str(factory(sock))
            except TypeError:
                candidate = str(factory())
        candidate = candidate.strip() or secrets.token_hex(4)
        with self._lock:
            if candidate not in self._sessions_by_id:
                return candidate
            while True:
                unique_candidate = f"{candidate}-{secrets.token_hex(2)}"
                if unique_candidate not in self._sessions_by_id:
                    return unique_candidate

    def _resolve_session(self, target: Any) -> ClientSession | None:
        if isinstance(target, ClientSession):
            with self._lock:
                current = self._sessions_by_id.get(target.client_id)
                return target if current is target else None
        with self._lock:
            try:
                session = self._sessions_by_socket.get(target)
            except TypeError:
                session = None
            if session is not None:
                return session
            return self._sessions_by_id.get(str(target))

    def _remove_session(self, session: ClientSession, *, close_socket: bool) -> bool:
        with self._lock:
            current = self._sessions_by_id.get(session.client_id)
            if current is not session:
                return False
            self._sessions_by_id.pop(session.client_id, None)
            self._sessions_by_socket.pop(session.socket, None)
        if close_socket:
            session.close()
        else:
            session.mark_closed()
        self._invoke_session_callback(self.on_close, session, "close")
        return True

    @staticmethod
    def _invoke_session_callback(
        callback: SessionCallback | None,
        session: ClientSession,
        label: str,
    ) -> None:
        if callback is None:
            return
        try:
            callback(session)
        except Exception:
            logger.exception("WebSocket %s callback failed for %s", label, session.client_id)
