"""Per-connection state for the WebSocket transport layer."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class ClientSession:
    """Own mutable state and serialized writes for one accepted socket."""

    socket: Any
    client_id: str
    page: str = "unknown"
    workspace_id: str | None = None
    wire_mode: str = "legacy"
    source_subscriptions: set[str] | None = None
    vibe: bool = False
    lyric: bool = False
    lyric_explicit: bool = False
    spectrum: bool = False
    spectrum_fps: int = 24
    spectrum_last_sent_at: float = 0.0
    closed: bool = False
    subscriptions: dict[str, Any] = field(default_factory=dict)
    send_lock: Any = field(default_factory=threading.RLock, repr=False)

    @property
    def id(self) -> str:
        """Compatibility alias for code that previously stored ``state['id']``."""
        return self.client_id

    @property
    def sock(self) -> Any:
        """Compatibility alias for socket-oriented hub code."""
        return self.socket

    @property
    def subscription_state(self) -> dict[str, Any]:
        """Lightweight container for ordinary channel/subscription bookkeeping."""
        return self.subscriptions

    @property
    def source_subscription_mode(self) -> str:
        return "legacy" if self.source_subscriptions is None else "explicit"

    @property
    def connected(self) -> bool:
        with self.send_lock:
            return not self.closed and bool(getattr(self.socket, "connected", True))

    def send_json(self, payload: Any) -> bool:
        """Serialize and send one JSON message without interleaving socket writes."""
        data = json.dumps(payload, ensure_ascii=False)

        with self.send_lock:
            if self.closed:
                return False
            try:
                self.socket.send(data)
                return True
            except Exception:
                return False

    def mark_closed(self) -> bool:
        """Mark the session closed once and report whether this call changed it."""
        with self.send_lock:
            if self.closed:
                return False
            self.closed = True
            return True

    def close(self) -> bool:
        """Close the underlying socket at most once."""
        if not self.mark_closed():
            return False
        try:
            close = getattr(self.socket, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
        return True

    def metadata(self) -> dict[str, Any]:
        """Return a lightweight mutable-state snapshot for hub migration code."""
        with self.send_lock:
            return {
                "id": self.client_id,
                "page": self.page,
                "workspace_id": self.workspace_id,
                "wire_mode": self.wire_mode,
                "source_subscription_mode": self.source_subscription_mode,
                "source_subscriptions": (
                    None if self.source_subscriptions is None else set(self.source_subscriptions)
                ),
                "vibe": self.vibe,
                "lyric": self.lyric,
                "lyric_explicit": self.lyric_explicit,
                "spectrum": self.spectrum,
                "spectrum_fps": self.spectrum_fps,
                "spectrum_last_sent_at": self.spectrum_last_sent_at,
                "closed": self.closed,
                "subscriptions": dict(self.subscriptions),
                "subscription_state": dict(self.subscriptions),
            }

    def list_payload(self) -> dict[str, Any]:
        """Return the stable public shape used by client/session listings."""
        with self.send_lock:
            workspace_id = self.workspace_id
            if workspace_id is None and self.page == "dashboard":
                workspace_id = "main"
            return {
                "id": self.client_id,
                "page": self.page,
                "workspace_id": workspace_id,
                "connected": not self.closed and bool(getattr(self.socket, "connected", True)),
                "sources": (
                    None
                    if self.source_subscriptions is None
                    else sorted(self.source_subscriptions)
                ),
                "wire_mode": self.wire_mode,
            }

    def metadata_payload(self) -> dict[str, Any]:
        return self.metadata()

    def to_list_payload(self) -> dict[str, Any]:
        return self.list_payload()

    def as_state(self) -> dict[str, Any]:
        """Alias exposing the legacy state-dict-compatible metadata snapshot."""
        return self.metadata()
