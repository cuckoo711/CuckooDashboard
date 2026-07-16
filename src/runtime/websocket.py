"""Dashboard WebSocket client state and background broadcasters."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from core.config import load_config, set_config_value
from features.appearance.service import get_font_payload
from features.dashboard.service import get_dashboard_data
from services.github_service import get_github_data
from services.media_service import get_lyric_frame, get_media_info
from services.spectrum_service import (
    acquire_spectrum,
    get_spectrum_frame,
    load_music_offsets,
    release_spectrum,
)
from services.system_service import get_system_info
from services.theme import load_theme_index, theme_response

logger = logging.getLogger("cuckoo.runtime.websocket")

_LYRIC_POLL_INTERVAL_S = 0.12
_SPECTRUM_FPS_MIN = 12
_SPECTRUM_FPS_MAX = 60
_SPECTRUM_FPS_DEFAULT = 24


def _load_vibe_state() -> bool:
    return bool(load_config().get("vibe_active", False))


def _save_vibe_state(active: bool) -> None:
    set_config_value("vibe_active", bool(active))


def _dashboard_media_payload(frame: dict) -> dict:
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
    """Own all WebSocket clients, protocol handling and broadcaster workers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._clients: list[Any] = []
        self._states: dict[Any, dict[str, Any]] = {}
        self._vibe = _load_vibe_state()
        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._lyric_last_push_key: tuple[str, int, str, str] | None = None
        self._started_at: float | None = None

    def register(self, sock: Any) -> None:
        """Register the ``/ws`` route or handle one accepted socket connection."""
        # Application factories pass the Flask-Sock extension itself, while the
        # registered route later calls this method with an accepted WebSocket.
        if callable(getattr(sock, "route", None)) and not callable(getattr(sock, "receive", None)):
            sock.route("/ws")(self.register)
            return

        self.start()
        saved_vibe = _load_vibe_state()
        client_id = hashlib.md5(str(id(sock)).encode()).hexdigest()[:8]
        with self._lock:
            if sock not in self._states:
                self._clients.append(sock)
            self._states[sock] = {
                "vibe": saved_vibe,
                "spectrum": False,
                "spectrum_fps": _SPECTRUM_FPS_DEFAULT,
                "spectrum_last_sent_at": 0.0,
                "lyric": False,
                "id": client_id,
                "page": "unknown",
            }
            self._recalc_vibe_locked()
            total = len(self._clients)
        logger.info("[ws] client connected (total: %s, id: %s)", total, client_id)
        self._send(sock, {"type": "connected", "id": client_id})
        self._submit_initial_push(sock)

        try:
            while not self._stop_event.is_set() and bool(getattr(sock, "connected", True)):
                raw = sock.receive(timeout=30)
                if raw:
                    self._handle_message(sock, client_id, raw)
        except Exception:
            pass
        finally:
            removed = self._remove_client(sock)
            if removed:
                logger.info("[ws] client disconnected (total: %s)", len(self.list_clients()))

    def start(self) -> bool:
        """Start all broadcaster threads once; stopped hubs may be started again."""
        with self._lifecycle_lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                return False
            self._threads = {}
            self._stop_event.clear()
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ws-fetch")
            workers = {
                "broadcaster": self._broadcaster_loop,
                "spectrum": self._spectrum_loop,
                "lyric": self._lyric_loop,
            }
            for name, target in workers.items():
                thread = threading.Thread(target=target, daemon=True, name=f"ws-{name}")
                self._threads[name] = thread
                thread.start()
            self._started_at = time.time()
            return True

    def stop(self, timeout: float = 5) -> None:
        """Stop workers, disconnect clients and shut down the fetch executor."""
        timeout = max(0.0, float(timeout))
        with self._lifecycle_lock:
            self._stop_event.set()
            sockets = self._client_sockets()
            for sock in sockets:
                try:
                    close = getattr(sock, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
                self._remove_client(sock)

            deadline = time.monotonic() + timeout
            for thread in list(self._threads.values()):
                if thread is threading.current_thread():
                    continue
                remaining = max(0.0, deadline - time.monotonic())
                thread.join(remaining)
            self._threads = {
                name: thread for name, thread in self._threads.items() if thread.is_alive()
            }

            executor = self._executor
            self._executor = None
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            if not self._threads:
                self._started_at = None

    def health(self) -> dict:
        with self._lock:
            clients = len(self._states)
            spectrum_clients = sum(1 for state in self._states.values() if state.get("spectrum"))
            lyric_clients = self._lyric_interest_count_locked()
        with self._lifecycle_lock:
            threads = {name: thread.is_alive() for name, thread in self._threads.items()}
            running = bool(threads) and all(threads.values()) and not self._stop_event.is_set()
            executor_alive = self._executor is not None
        return {
            "status": "ok" if running else "stopped",
            "ok": running,
            "running": running,
            "clients": clients,
            "spectrum_clients": spectrum_clients,
            "lyric_clients": lyric_clients,
            "threads": threads,
            "executor": executor_alive,
            "started_at": self._started_at,
        }

    def broadcast(self, msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        for sock in self._client_sockets():
            try:
                sock.send(data)
            except Exception:
                dead.append(sock)
        for sock in dead:
            self._remove_client(sock)

    def broadcast_media(self, frame: dict) -> None:
        full_data = json.dumps({"type": "media", "data": frame}, ensure_ascii=False)
        slim_data = json.dumps(
            {"type": "media", "data": _dashboard_media_payload(frame)},
            ensure_ascii=False,
        )
        dead = []
        for sock in self._client_sockets():
            with self._lock:
                page = (self._states.get(sock) or {}).get("page") or "unknown"
            try:
                sock.send(slim_data if page == "dashboard" else full_data)
            except Exception:
                dead.append(sock)
        for sock in dead:
            self._remove_client(sock)

    def broadcast_lyric(self, msg: dict) -> int:
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        sent = 0
        for sock in self._client_sockets():
            with self._lock:
                state = self._states.get(sock) or {}
                interested = bool(state.get("lyric")) or state.get("page") in {"music", "dashboard"}
            if not interested:
                continue
            try:
                sock.send(data)
                sent += 1
            except Exception:
                dead.append(sock)
        for sock in dead:
            self._remove_client(sock)
        return sent

    def broadcast_settings_update(self) -> None:
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

    def list_clients(self) -> list:
        with self._lock:
            return [
                {
                    "id": state.get("id", "?"),
                    "page": state.get("page", "unknown"),
                    "connected": bool(getattr(sock, "connected", False)),
                }
                for sock, state in self._states.items()
            ]

    def navigate_client(self, client_id: str, page: str) -> bool:
        if page not in {"dashboard", "music"}:
            return False
        target = self._find_client(client_id)
        if target is None:
            return False
        url = "/" if page == "dashboard" else "/music"
        if not self._send(target, {"type": "navigate", "page": page, "url": url}):
            self._remove_client(target)
            return False
        return True

    def request_screenshot(self, client_id: str) -> str | None:
        target = self._find_client(client_id)
        if target is None:
            return None
        request_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        if not self._send(target, {"type": "screenshot", "request_id": request_id}):
            self._remove_client(target)
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
            for state in self._states.values():
                state["vibe"] = active
        self.broadcast({"type": "vibe_state", "data": {"active": active}})
        return active

    def force_lyric_sync(self) -> None:
        try:
            frame = get_lyric_frame()
            with self._lock:
                self._lyric_last_push_key = self._lyric_key(frame)
            self.broadcast_lyric({"type": "lyric", "data": frame})
        except Exception as exc:
            logger.debug("[ws] forced lyric sync failed: %s", exc)

    def _submit_initial_push(self, sock: Any) -> None:
        with self._lifecycle_lock:
            executor = self._executor
        if executor is not None:
            try:
                executor.submit(self._send_all_data, sock)
                return
            except RuntimeError:
                pass
        threading.Thread(target=self._send_all_data, args=(sock,), daemon=True).start()

    def _send_all_data(self, sock: Any) -> None:
        def safe_send(msg_type: str, getter) -> None:
            try:
                self._send(sock, {"type": msg_type, "data": getter()})
                logger.info("[ws] init: sent %s", msg_type)
            except Exception as exc:
                logger.error("[ws] init %s fetch error: %s", msg_type, exc)

        self._send(sock, {"type": "vibe_state", "data": {"active": _load_vibe_state()}})
        safe_send("dashboard_data", get_dashboard_data)
        safe_send("github", get_github_data)
        safe_send("media", get_media_info)
        safe_send("system", get_system_info)
        try:
            self._send(sock, {"type": "theme", "data": theme_response(load_theme_index())})
        except Exception:
            pass
        safe_send("font", get_font_payload)
        logger.info("[ws] init: all data sent")

    def _handle_message(self, sock: Any, client_id: str, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        try:
            msg_type = msg.get("type")
            if msg_type == "vibe":
                with self._lock:
                    state = self._states.get(sock)
                    if state is None:
                        return
                    state["vibe"] = bool(msg.get("active"))
                    vibe = self._recalc_vibe_locked()
                _save_vibe_state(vibe)
                logger.info("[ws] vibe coding: %s", "ON" if vibe else "OFF")
            elif msg_type == "report":
                page = str(msg.get("page") or "unknown")
                with self._lock:
                    state = self._states.get(sock)
                    if state is None:
                        return
                    state["page"] = page
                    if page in {"music", "dashboard"}:
                        state["lyric"] = True
                logger.info("[ws] client %s reports page: %s", client_id, page)
            elif msg_type == "subscribe":
                channel = str(msg.get("channel") or "")
                if channel == "spectrum":
                    active = bool(msg.get("active"))
                    self._set_spectrum_interest(sock, active, msg.get("fps"))
                    if active:
                        if self._send(sock, {"type": "spectrum", "data": get_spectrum_frame()}):
                            self._send(sock, {"type": "music_offset", "data": load_music_offsets()})
                            with self._lock:
                                state = self._states.get(sock)
                                if state is not None:
                                    state["spectrum_last_sent_at"] = time.monotonic()
                elif channel == "lyric":
                    active = bool(msg.get("active"))
                    with self._lock:
                        state = self._states.get(sock)
                        if state is not None:
                            state["lyric"] = active
                    if active:
                        self._send(sock, {"type": "lyric", "data": get_lyric_frame()})
            elif msg_type == "init":
                self._send_all_data(sock)
            elif msg_type == "ping":
                self._send(sock, {"type": "pong", "ts": msg.get("ts")})
            elif msg_type == "screenshot_data":
                self.broadcast({
                    "type": "screenshot_result",
                    "request_id": msg.get("request_id"),
                    "client_id": client_id,
                    "data": msg.get("data"),
                    "timestamp": time.time(),
                })
                logger.info("[ws] screenshot received from %s", client_id)
        except (KeyError, TypeError, ValueError):
            return

    def _broadcaster_loop(self) -> None:
        vibe_counter = 0
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                if self._has_clients():
                    with self._lifecycle_lock:
                        executor = self._executor
                    if executor is None:
                        break
                    futures = {
                        executor.submit(get_system_info): "system",
                        executor.submit(get_media_info): "media",
                        executor.submit(get_github_data): "github",
                    }
                    for future in as_completed(futures):
                        if self._stop_event.is_set():
                            break
                        msg_type = futures[future]
                        try:
                            result = future.result()
                            if msg_type == "media":
                                self.broadcast_media(result)
                            else:
                                self.broadcast({"type": msg_type, "data": result})
                        except Exception as exc:
                            logger.error("[ws] %s broadcast error: %s", msg_type, exc)
                    vibe_counter += 1
                    interval = 20 if self.get_vibe() else 60
                    if vibe_counter >= interval:
                        vibe_counter = 0
                        self.broadcast({"type": "dashboard_data", "data": get_dashboard_data()})
            except Exception as exc:
                logger.error("[ws] broadcaster error: %s", exc)
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.0, 1.0 - elapsed))

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
        targets = []
        with self._lock:
            for sock, state in self._states.items():
                if not state.get("spectrum"):
                    continue
                fps = _clamp_spectrum_fps(state.get("spectrum_fps", _SPECTRUM_FPS_DEFAULT))
                last_sent = float(state.get("spectrum_last_sent_at") or 0.0)
                if now - last_sent + 1e-6 < 1.0 / fps:
                    continue
                state["spectrum_last_sent_at"] = now
                targets.append(sock)
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        sent = 0
        for sock in targets:
            try:
                sock.send(data)
                sent += 1
            except Exception:
                dead.append(sock)
        for sock in dead:
            self._remove_client(sock)
        return sent

    def _set_spectrum_interest(self, sock: Any, active: bool, fps: Any = None) -> None:
        with self._lock:
            state = self._states.get(sock)
            if state is None:
                return
            was_active = bool(state.get("spectrum"))
            if active:
                state["spectrum_fps"] = _clamp_spectrum_fps(
                    fps if fps is not None else state.get("spectrum_fps", _SPECTRUM_FPS_DEFAULT)
                )
                state["spectrum_last_sent_at"] = 0.0
            state["spectrum"] = bool(active)
            requested_fps = state.get("spectrum_fps")
        if was_active == bool(active):
            return
        if active:
            acquire_spectrum()
            logger.info("[ws] spectrum subscribe ON (%sfps)", requested_fps)
        else:
            release_spectrum()
            logger.info("[ws] spectrum subscribe OFF")

    def _remove_client(self, sock: Any) -> bool:
        """Remove a client exactly once and release its spectrum ref exactly once."""
        with self._lock:
            state = self._states.pop(sock, None)
            if state is None:
                return False
            try:
                self._clients.remove(sock)
            except ValueError:
                pass
            had_spectrum = bool(state.get("spectrum"))
            self._recalc_vibe_locked()
        if had_spectrum:
            release_spectrum()
        return True

    def _recalc_vibe_locked(self) -> bool:
        if self._states:
            self._vibe = any(bool(state.get("vibe")) for state in self._states.values())
        else:
            self._vibe = _load_vibe_state()
        return self._vibe

    def _client_sockets(self) -> list[Any]:
        with self._lock:
            return list(self._clients)

    def _has_clients(self) -> bool:
        with self._lock:
            return bool(self._clients)

    def _find_client(self, client_id: str) -> Any | None:
        with self._lock:
            for sock, state in self._states.items():
                if state.get("id") == client_id:
                    return sock
        return None

    def _spectrum_target_fps(self) -> int:
        with self._lock:
            requested = [
                _clamp_spectrum_fps(state.get("spectrum_fps", _SPECTRUM_FPS_DEFAULT))
                for state in self._states.values()
                if state.get("spectrum")
            ]
        return max(requested, default=0)

    def _lyric_interest_count(self) -> int:
        with self._lock:
            return self._lyric_interest_count_locked()

    def _lyric_interest_count_locked(self) -> int:
        return sum(
            1
            for state in self._states.values()
            if state.get("lyric") or state.get("page") in {"music", "dashboard"}
        )

    @staticmethod
    def _lyric_key(frame: dict) -> tuple[str, int, str, str]:
        return (
            str(frame.get("track_key") or ""),
            int(frame.get("lyric_index", -1) if frame.get("lyric_index") is not None else -1),
            str(frame.get("status") or ""),
            str(frame.get("lyric") or ""),
        )

    @staticmethod
    def _send(sock: Any, msg: dict) -> bool:
        try:
            sock.send(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception:
            return False
