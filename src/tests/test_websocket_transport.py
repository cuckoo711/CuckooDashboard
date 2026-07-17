"""Tests for the pure WebSocket transport/session layer."""

from __future__ import annotations

import inspect
import json
import threading
import time

from runtime.client_session import ClientSession
from runtime.websocket_transport import WebSocketTransport


_DISCONNECT = object()


class _Socket:
    def __init__(self, received=()):
        self.connected = True
        self.received = list(received)
        self.sent = []
        self.close_calls = 0

    def receive(self, timeout=30):
        assert timeout == 30
        if not self.received:
            self.connected = False
            return None
        item = self.received.pop(0)
        if item is _DISCONNECT:
            self.connected = False
            return None
        if isinstance(item, BaseException):
            raise item
        return item

    def send(self, data):
        self.sent.append(json.loads(data))

    def close(self):
        self.close_calls += 1
        self.connected = False


class _SockExtension:
    def __init__(self):
        self.path = None
        self.handler = None

    def route(self, path):
        self.path = path

        def decorate(handler):
            self.handler = handler
            return handler

        return decorate


def test_register_adds_ws_route_and_handler_accepts_socket():
    opened = []
    transport = WebSocketTransport(on_open=opened.append, client_id_factory=lambda: "client-1")
    extension = _SockExtension()

    transport.register(extension)

    assert extension.path == "/ws"
    assert extension.handler == transport.register

    socket = _Socket([_DISCONNECT])
    extension.handler(socket)
    assert [session.client_id for session in opened] == ["client-1"]


def test_invalid_json_is_silently_ignored_and_valid_object_is_delivered():
    messages = []
    closed = []
    socket = _Socket([
        TimeoutError(),
        "{not-json",
        "[]",
        json.dumps({"type": "ping"}),
        _DISCONNECT,
    ])
    transport = WebSocketTransport(
        on_message=lambda session, message: messages.append((session.client_id, message)),
        on_close=closed.append,
        client_id_factory=lambda: "client-json",
    )

    transport.register(socket)

    assert messages == [("client-json", {"type": "ping"})]
    assert [session.client_id for session in closed] == ["client-json"]
    assert transport.list_sessions() == []


class _SerialSocket(_Socket):
    def __init__(self):
        super().__init__()
        self.active_sends = 0
        self.max_active_sends = 0
        self.guard = threading.Lock()

    def send(self, data):
        with self.guard:
            self.active_sends += 1
            self.max_active_sends = max(self.max_active_sends, self.active_sends)
        time.sleep(0.01)
        self.sent.append(json.loads(data))
        with self.guard:
            self.active_sends -= 1


def test_session_send_lock_serializes_concurrent_sends():
    socket = _SerialSocket()
    session = ClientSession(socket=socket, client_id="serial")
    barrier = threading.Barrier(9)

    def send(index):
        barrier.wait()
        assert session.send_json({"index": index}) is True

    threads = [threading.Thread(target=send, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert socket.max_active_sends == 1
    assert sorted(message["index"] for message in socket.sent) == list(range(8))


class _FailingSocket(_Socket):
    def send(self, _data):
        raise OSError("socket closed")


def test_send_failure_removes_closes_and_calls_close_once():
    closed = []
    socket = _FailingSocket()
    transport = WebSocketTransport(on_close=closed.append, client_id_factory=lambda: "dead")
    transport.start()
    session = ClientSession(socket=socket, client_id="dead")
    transport._sessions_by_socket[socket] = session
    transport._sessions_by_id[session.client_id] = session

    assert transport.send_to("dead", {"type": "one"}) is False
    assert transport.send_to("dead", {"type": "two"}) is False

    assert socket.close_calls == 1
    assert closed == [session]
    assert transport.list_sessions() == []


def test_disconnect_exception_cleans_up_once():
    opened = []
    closed = []
    socket = _Socket([OSError("gone")])
    transport = WebSocketTransport(
        on_open=opened.append,
        on_close=closed.append,
        client_id_factory=lambda: "disconnect",
    )

    transport.register(socket)

    assert len(opened) == 1
    assert closed == opened
    assert opened[0].closed is True
    assert socket.close_calls == 1
    assert transport.health()["clients"] == 0


def test_start_stop_and_restart_close_current_sessions():
    closed = []
    socket = _Socket()
    session = ClientSession(socket=socket, client_id="live")
    transport = WebSocketTransport(on_close=closed.append)

    assert transport.health()["running"] is False
    assert transport.start() is True
    assert transport.start() is False
    assert transport.health()["running"] is True
    transport._sessions_by_socket[socket] = session
    transport._sessions_by_id[session.client_id] = session

    transport.stop()
    assert transport.health()["running"] is False
    assert transport.health()["clients"] == 0
    assert socket.close_calls == 1
    assert closed == [session]

    assert transport.start() is True
    assert transport.health()["running"] is True
    assert transport.restart() is True
    assert transport.health()["running"] is True
    transport.stop()


def test_transport_has_no_workspace_registry_or_business_protocol_dependency():
    source = inspect.getsource(__import__("runtime.websocket_transport", fromlist=["*"]))
    forbidden = [
        "WorkspaceRegistry",
        "workspace_registry",
        "legacy_message_type",
        "refresh_policy",
        "source_id",
        "get_spectrum_frame",
    ]

    assert all(name not in source for name in forbidden)
    assert list(inspect.signature(WebSocketTransport).parameters) == [
        "on_message",
        "on_open",
        "on_close",
        "path",
        "client_id_factory",
    ]


def test_session_metadata_and_list_payload_support_legacy_mirroring():
    socket = _Socket()
    session = ClientSession(
        socket=socket,
        client_id="metadata",
        page="dashboard",
        source_subscriptions={"system.snapshot"},
        lyric=True,
        spectrum=True,
        spectrum_fps=30,
    )
    session.subscriptions["custom"] = {"active": True}

    metadata = session.metadata()
    listing = session.list_payload()

    assert metadata["id"] == "metadata"
    assert metadata["source_subscription_mode"] == "explicit"
    assert metadata["source_subscriptions"] == {"system.snapshot"}
    assert metadata["subscriptions"] == {"custom": {"active": True}}
    assert listing == {
        "id": "metadata",
        "page": "dashboard",
        "workspace_id": "main",
        "viewport_width": None,
        "viewport_height": None,
        "workspace_width": None,
        "workspace_height": None,
        "device_pixel_ratio": None,
        "visual_viewport_scale": None,
        "connected": True,
        "sources": ["system.snapshot"],
        "wire_mode": "legacy",
    }
