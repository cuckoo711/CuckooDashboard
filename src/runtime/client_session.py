"""Per-connection state for the WebSocket transport layer."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


_MAX_CSS_VIEWPORT_PX = 65536.0
_MIN_DEVICE_SCALE = 0.25
_MAX_DEVICE_SCALE = 8.0


class ViewportContractError(ValueError):
    """Raised when a client sends an invalid CSS viewport report."""


def _finite_number(payload: Mapping[str, Any], key: str, *, minimum: float, maximum: float) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ViewportContractError(f"viewport.{key} must be a number")
    value = float(value)
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ViewportContractError(
            f"viewport.{key} must be between {minimum:g} and {maximum:g}"
        )
    return round(value, 3)


def normalize_viewport_payload(
    payload: Any,
    *,
    require_workspace: bool = True,
) -> dict[str, float]:
    """Validate a browser report and return normalized CSS-pixel measurements."""
    if not isinstance(payload, Mapping):
        raise ViewportContractError("viewport must be an object")
    width = _finite_number(payload, "width", minimum=1.0, maximum=_MAX_CSS_VIEWPORT_PX)
    height = _finite_number(payload, "height", minimum=1.0, maximum=_MAX_CSS_VIEWPORT_PX)
    if require_workspace and (
        "workspace_width" not in payload or "workspace_height" not in payload
    ):
        raise ViewportContractError(
            "dashboard viewport must include workspace_width and workspace_height"
        )
    workspace_width = _finite_number(
        {"workspace_width": payload.get("workspace_width", width)},
        "workspace_width",
        minimum=1.0,
        maximum=_MAX_CSS_VIEWPORT_PX,
    )
    workspace_height = _finite_number(
        {"workspace_height": payload.get("workspace_height", height)},
        "workspace_height",
        minimum=1.0,
        maximum=_MAX_CSS_VIEWPORT_PX,
    )
    device_pixel_ratio = _finite_number(
        {"device_pixel_ratio": payload.get("device_pixel_ratio", 1.0)},
        "device_pixel_ratio",
        minimum=_MIN_DEVICE_SCALE,
        maximum=_MAX_DEVICE_SCALE,
    )
    visual_viewport_scale = _finite_number(
        {"visual_viewport_scale": payload.get("visual_viewport_scale", 1.0)},
        "visual_viewport_scale",
        minimum=_MIN_DEVICE_SCALE,
        maximum=_MAX_DEVICE_SCALE,
    )
    return {
        "width": width,
        "height": height,
        "workspace_width": workspace_width,
        "workspace_height": workspace_height,
        "device_pixel_ratio": device_pixel_ratio,
        "visual_viewport_scale": visual_viewport_scale,
    }


@dataclass(eq=False)
class ClientSession:
    """Own mutable state and serialized writes for one accepted socket."""

    socket: Any
    client_id: str
    page: str = "unknown"
    workspace_id: str | None = None
    viewport_width: float | None = None
    viewport_height: float | None = None
    workspace_width: float | None = None
    workspace_height: float | None = None
    device_pixel_ratio: float | None = None
    visual_viewport_scale: float | None = None
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

    def set_viewport(self, viewport: Mapping[str, float]) -> None:
        """Store one already-normalized CSS viewport report."""
        with self.send_lock:
            self.viewport_width = viewport["width"]
            self.viewport_height = viewport["height"]
            self.workspace_width = viewport["workspace_width"]
            self.workspace_height = viewport["workspace_height"]
            self.device_pixel_ratio = viewport["device_pixel_ratio"]
            self.visual_viewport_scale = viewport["visual_viewport_scale"]

    def clear_viewport(self) -> None:
        """Clear dashboard-only geometry when the client navigates elsewhere."""
        with self.send_lock:
            self.viewport_width = None
            self.viewport_height = None
            self.workspace_width = None
            self.workspace_height = None
            self.device_pixel_ratio = None
            self.visual_viewport_scale = None

    def viewport_payload(self) -> dict[str, float | None]:
        """Return the latest browser geometry in the public client shape."""
        with self.send_lock:
            return {
                "viewport_width": self.viewport_width,
                "viewport_height": self.viewport_height,
                "workspace_width": self.workspace_width,
                "workspace_height": self.workspace_height,
                "device_pixel_ratio": self.device_pixel_ratio,
                "visual_viewport_scale": self.visual_viewport_scale,
            }

    def metadata(self) -> dict[str, Any]:
        """Return a lightweight mutable-state snapshot for hub migration code."""
        with self.send_lock:
            return {
                "id": self.client_id,
                "page": self.page,
                "workspace_id": self.workspace_id,
                **self.viewport_payload(),
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
                **self.viewport_payload(),
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
