"""Transport-neutral subscription broker compatibility tests."""

from __future__ import annotations

from contracts.workspace import DataSourceDescriptor, DataSourceRefreshPolicy
from runtime.refresh_scheduler import RefreshScheduler
from runtime.subscription_broker import SubscriptionBroker
from workspaces.data_sources import DataSourceDefinition
from workspaces.registry import CORE_OWNER_ID, RegistryOwner, WorkspaceRegistry


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Session:
    def __init__(self):
        self.messages = []

    def send_json(self, payload):
        self.messages.append(payload)


def _policy(default_ms=1000, minimum_ms=100):
    return DataSourceRefreshPolicy(
        default_interval_ms=default_ms,
        minimum_interval_ms=minimum_ms,
        cache_ttl_ms=default_ms,
        stale_if_error_ms=5000,
        error_backoff_initial_ms=default_ms,
        error_backoff_max_ms=max(default_ms, 60000),
    )


def _source(source_id, getter, legacy_message_type=None, policy=None):
    policy = policy or _policy()
    return DataSourceDefinition(
        descriptor=DataSourceDescriptor(
            id=source_id,
            kind="snapshot",
            legacy_message_type=legacy_message_type,
            default_interval_seconds=policy.default_interval_ms / 1000,
            refresh_policy=policy,
        ),
        getter=getter,
    )


def _registry(getters=None):
    getters = getters or {}
    registry = WorkspaceRegistry()
    registry.register_owner(RegistryOwner(CORE_OWNER_ID, locked=True))
    registry.register_data_source(
        _source("system.snapshot", getters.get("system", lambda: {"cpu": 1}), "system"),
        owner_id=CORE_OWNER_ID,
    )
    registry.register_data_source(
        _source("media.playback", getters.get("media", lambda: {"title": "Song"}), "media"),
        owner_id=CORE_OWNER_ID,
    )
    registry.register_owner(
        RegistryOwner("com.example.extension", dependencies=(CORE_OWNER_ID,))
    )
    registry.register_data_source(
        _source(
            "com.example.extension.health",
            getters.get("health", lambda: {"status": "ok"}),
        ),
        owner_id="com.example.extension",
    )
    return registry


def _broker(*, getters=None, available=None):
    clock = FakeClock()
    registry = _registry(getters)
    scheduler = RefreshScheduler(registry, clock=clock, wall_clock=lambda: 100.0)
    broker = SubscriptionBroker(
        registry,
        scheduler,
        clock=clock,
        is_owner_available=available,
    )
    return clock, registry, scheduler, broker


def test_new_card_subscription_rejects_nonempty_query_with_stable_error():
    _, _, _, broker = _broker()
    session = Session()
    broker.register_session(session, legacy_all=False)

    handled = broker.handle_message(
        session,
        {
            "type": "subscribe",
            "subscriptions": [
                {
                    "id": "health-card",
                    "source_id": "com.example.extension.health",
                    "query": {"host": "secret"},
                }
            ],
        },
    )

    assert handled is False
    assert broker.subscriptions_for_session(session) == ()
    assert session.messages[-1]["type"] == "source_error"
    assert session.messages[-1]["error"]["code"] == "source_query_not_supported"
    assert session.messages[-1]["error"]["subscription_id"] == "health-card"


def test_card_delivery_interval_replay_and_canonical_snapshot_envelope():
    clock, _, _, broker = _broker()
    session = Session()
    broker.register_session(session, legacy_all=False)
    assert broker.handle_message(
        session,
        {
            "type": "subscribe",
            "subscriptions": [
                {
                    "id": "health-card",
                    "source_id": "com.example.extension.health",
                    "interval_ms": 500,
                    "query": {},
                }
            ],
        },
    )

    broker.publish_external("com.example.extension.health", {"value": 1})
    broker.publish_external("com.example.extension.health", {"value": 2})
    assert len(session.messages) == 1
    assert session.messages[0] == {
        "v": 1,
        "type": "data.snapshot",
        "subscriptionId": "health-card",
        "channel": "com.example.extension.health",
        "sequence": 1,
        "timestamp": "1970-01-01T00:01:40Z",
        "data": {"value": 1},
        "meta": {
            "status": "ok",
            "stale": False,
            "sampledAtMs": 100000,
            "error": None,
        },
    }

    clock.advance(0.5)
    broker.publish_external("com.example.extension.health", {"value": 3})
    assert session.messages[-1]["data"] == {"value": 3}

    replay = Session()
    broker.register_session(replay, legacy_all=False)
    broker.handle_message(
        replay,
        {
            "type": "subscribe",
            "subscriptions": [
                {
                    "id": "replay-card",
                    "source_id": "com.example.extension.health",
                }
            ],
        },
    )
    assert replay.messages[-1]["data"] == {"value": 3}


def test_legacy_replace_add_remove_and_single_sample_fanout():
    calls = []
    clock, _, scheduler, broker = _broker(
        getters={"system": lambda: calls.append(True) or {"cpu": len(calls)}}
    )
    first = Session()
    second = Session()
    broker.register_session(first, legacy_all=False)
    broker.register_session(second, legacy_all=False)

    assert broker.handle_message(
        first,
        {"type": "subscribe", "sources": ["system.snapshot"], "replace": True},
    )
    assert broker.handle_message(
        second,
        {"type": "subscribe", "sources": ["system.snapshot"], "replace": True},
    )
    scheduler.run_due(wait=True)
    assert calls == [True]
    assert first.messages[-1] == {"type": "system", "data": {"cpu": 1}}
    assert second.messages[-1] == {"type": "system", "data": {"cpu": 1}}

    assert broker.handle_message(
        first,
        {"type": "subscribe", "sources": ["media.playback"], "replace": False},
    )
    assert {item.source_id for item in broker.subscriptions_for_session(first)} == {
        "system.snapshot",
        "media.playback",
    }
    assert broker.handle_message(
        first,
        {"type": "unsubscribe", "sources": ["system.snapshot"]},
    )
    assert {item.source_id for item in broker.subscriptions_for_session(first)} == {
        "media.playback"
    }

    first_message_count = len(first.messages)
    clock.advance(1)
    broker.publish_external("system.snapshot", {"cpu": 2})
    assert len(first.messages) == first_message_count
    assert second.messages[-1] == {"type": "system", "data": {"cpu": 2}}


def test_media_dashboard_is_slim_music_is_full_and_owner_availability_is_checked():
    availability = {
        CORE_OWNER_ID: True,
        "com.example.extension": True,
    }
    _, _, _, broker = _broker(available=lambda owner: availability.get(owner, False))
    dashboard = Session()
    music = Session()
    extension = Session()
    broker.register_session(dashboard, page="dashboard", legacy_all=False)
    broker.register_session(music, page="music", legacy_all=False)
    broker.register_session(extension, page="dashboard", legacy_all=False)
    broker.handle_message(
        dashboard,
        {"type": "subscribe", "sources": ["media.playback"]},
    )
    broker.handle_message(
        music,
        {"type": "subscribe", "sources": ["media.playback"]},
    )
    broker.handle_message(
        extension,
        {
            "type": "subscribe",
            "subscriptions": [
                {
                    "id": "health",
                    "source_id": "com.example.extension.health",
                }
            ],
        },
    )

    frame = {
        "title": "Song",
        "lyrics": [[0, "line"]],
        "cover_palette": {"accent": "red"},
    }
    broker.publish_external("media.playback", frame)
    assert dashboard.messages[-1]["data"]["media_slim"] is True
    assert dashboard.messages[-1]["data"]["lyrics"] == []
    assert "cover_palette" not in dashboard.messages[-1]["data"]
    assert music.messages[-1]["data"] == frame

    availability["com.example.extension"] = False
    broker.publish_external("com.example.extension.health", {"status": "ok"})
    assert extension.messages[-1]["type"] == "source_error"
    assert extension.messages[-1]["error"]["code"] == "source_owner_unavailable"


def test_init_replay_send_callback_invalidate_and_close_are_idempotent():
    _, _, scheduler, broker = _broker()
    broker.publish_external("system.snapshot", {"cpu": 7})
    messages = []
    session = object()
    broker.register_session(
        session,
        send=messages.append,
        legacy_all=False,
        page="dashboard",
    )
    broker.handle_message(
        session,
        {"type": "subscribe", "sources": ["system.snapshot"]},
    )
    messages.clear()

    assert broker.handle_message(session, {"type": "init"}) is True
    assert messages == [{"type": "system", "data": {"cpu": 7}}]
    assert broker.invalidate("system.snapshot", refresh=False) is True
    assert scheduler.cache.get("system.snapshot") is None
    assert broker.close_session(session) is True
    assert broker.close_session(session) is False
    assert scheduler.effective_interval_ms("system.snapshot") is None
    broker.close()
    broker.close()
