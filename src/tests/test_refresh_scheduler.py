"""Demand-aware refresh scheduler and source cache tests."""

from __future__ import annotations

from contracts.subscription import SourceError
from contracts.workspace import DataSourceDescriptor, DataSourceRefreshPolicy
from runtime.refresh_scheduler import RefreshScheduler
from runtime.source_cache import SourceCache
from workspaces.data_sources import DataSourceDefinition


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _definition(getter, *, source_id="test.source", policy=None):
    policy = policy or DataSourceRefreshPolicy(
        default_interval_ms=1000,
        minimum_interval_ms=100,
        cache_ttl_ms=500,
        stale_if_error_ms=1000,
        error_backoff_initial_ms=200,
        error_backoff_max_ms=800,
    )
    return DataSourceDefinition(
        descriptor=DataSourceDescriptor(
            id=source_id,
            kind="snapshot",
            legacy_message_type=None,
            default_interval_seconds=policy.default_interval_ms / 1000,
            refresh_policy=policy,
        ),
        getter=getter,
    )


def test_source_cache_deepcopies_tracks_freshness_error_sequence_and_invalidate():
    clock = FakeClock()
    cache = SourceCache(clock=clock, wall_clock=lambda: 123.0)
    original = {"items": [1]}

    first = cache.record_success(
        "test.source",
        original,
        cache_ttl_ms=500,
        stale_if_error_ms=1000,
    )
    original["items"].append(2)
    assert first.sequence == 1
    assert first.sampled_at_ms == 123000
    assert cache.get("test.source").data == {"items": [1]}

    returned = cache.get("test.source")
    returned.data["items"].append(3)
    assert cache.get("test.source").data == {"items": [1]}

    clock.advance(0.5)
    assert cache.get_fresh("test.source") is not None
    clock.advance(0.001)
    assert cache.get_fresh("test.source") is None
    assert cache.get("test.source") is None

    error = SourceError.refresh_failed("test.source")
    stale = cache.record_error("test.source", error, stale_if_error_ms=1000)
    assert stale is not None
    assert stale.error == error
    assert stale.data == {"items": [1]}

    assert cache.invalidate("test.source") is True
    assert cache.get("test.source").stale is True
    second = cache.record_success("test.source", {"ok": True}, cache_ttl_ms=500)
    assert second.sequence == 2
    assert second.error is None


def test_scheduler_aggregates_demand_clamps_minimum_and_single_flights_fanout():
    clock = FakeClock()
    calls = []
    events = []
    scheduler = RefreshScheduler(
        [_definition(lambda: calls.append(True) or {"call": len(calls)})],
        clock=clock,
        wall_clock=lambda: 10.0,
    )
    scheduler.add_listener(events.append)
    scheduler.set_demand("card-a", "test.source", interval_ms=50)
    scheduler.set_demand("card-b", "test.source", interval_ms=500)

    assert scheduler.effective_interval_ms("test.source") == 100
    assert len(scheduler.run_due(wait=True)) == 1
    assert calls == [True]
    assert events[-1].snapshot.data == {"call": 1}
    assert scheduler.run_due(wait=True) == ()

    clock.advance(0.099)
    assert scheduler.run_due(wait=True) == ()
    clock.advance(0.001)
    assert len(scheduler.run_due(wait=True)) == 1
    assert calls == [True, True]

    scheduler.remove_demand("card-a", "test.source")
    scheduler.remove_demand("card-b", "test.source")
    assert scheduler.effective_interval_ms("test.source") is None
    clock.advance(10)
    assert scheduler.run_due(wait=True) == ()


def test_new_demand_reuses_fresh_cache_without_forcing_an_extra_sample():
    clock = FakeClock()
    calls = []
    scheduler = RefreshScheduler(
        [_definition(lambda: calls.append(True) or {"call": len(calls)})],
        clock=clock,
        wall_clock=lambda: 15.0,
    )
    scheduler.set_demand("first", "test.source")
    scheduler.run_due(wait=True)
    assert calls == [True]

    scheduler.set_demand("second", "test.source")
    assert scheduler.run_due(wait=True) == ()
    assert calls == [True]

    clock.advance(1.0)
    scheduler.run_due(wait=True)
    assert calls == [True, True]


def test_scheduler_keeps_last_success_and_applies_sanitized_exponential_backoff():
    clock = FakeClock()
    outcomes = [{"value": 1}, RuntimeError("secret token=abc"), {"value": 2}]

    def getter():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    scheduler = RefreshScheduler(
        [_definition(getter)],
        clock=clock,
        wall_clock=lambda: 20.0,
    )
    events = []
    scheduler.add_listener(events.append)
    scheduler.set_demand("card", "test.source")

    scheduler.run_due(wait=True)
    clock.advance(1.0)
    scheduler.run_due(wait=True)

    failed = events[-1]
    assert failed.error.code == "source_refresh_failed"
    assert "secret" not in failed.error.message
    assert failed.snapshot.data == {"value": 1}
    assert failed.snapshot.stale is True
    assert scheduler.health()["failures"] == {"test.source": 1}

    clock.advance(0.199)
    assert scheduler.run_due(wait=True) == ()
    clock.advance(0.001)
    scheduler.run_due(wait=True)
    assert scheduler.cache.get("test.source").data == {"value": 2}
    assert scheduler.health()["failures"] == {}


def test_reconnect_and_init_style_refresh_do_not_bypass_error_backoff():
    clock = FakeClock()
    outcomes = [RuntimeError("boom"), {"ok": True}]

    def getter():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    scheduler = RefreshScheduler(
        [_definition(getter)],
        clock=clock,
        wall_clock=lambda: 25.0,
    )
    scheduler.set_demand("first", "test.source")
    scheduler.run_due(wait=True)
    scheduler.remove_demand("first", "test.source")
    scheduler.set_demand("reconnected", "test.source")

    assert scheduler.refresh_if_due("test.source", wait=True) is None
    assert outcomes == [{"ok": True}]
    clock.advance(0.199)
    assert scheduler.run_due(wait=True) == ()
    clock.advance(0.001)
    scheduler.run_due(wait=True)
    assert scheduler.cache.get_fresh("test.source").data == {"ok": True}


def test_scheduler_external_publish_invalidate_and_lifecycle_are_idempotent():
    clock = FakeClock()
    scheduler = RefreshScheduler(
        [_definition(lambda: {"getter": True})],
        clock=clock,
        wall_clock=lambda: 30.0,
    )
    events = []
    scheduler.add_listener(events.append)

    snapshot = scheduler.publish_external("test.source", {"push": True})
    assert snapshot.sequence == 1
    assert events[-1].external is True
    assert scheduler.invalidate("test.source", refresh=False) is True
    assert scheduler.cache.get("test.source") is None

    assert scheduler.start() is True
    assert scheduler.start() is False
    assert scheduler.health()["running"] is True
    scheduler.stop()
    scheduler.stop()
    assert scheduler.health()["running"] is False
