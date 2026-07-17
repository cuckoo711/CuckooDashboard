"""Demand-aware deadline scheduler for registered workspace data sources."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from contracts.subscription import SourceError, SourceSnapshot
from contracts.workspace import DataSourceRefreshPolicy
from runtime.source_cache import SourceCache
from workspaces.data_sources import DataSourceDefinition


@dataclass(frozen=True)
class SourceDemand:
    """One consumer's requested refresh cadence for a source."""

    source_id: str
    interval_ms: int | None = None
    active: bool = False


@dataclass(frozen=True)
class RefreshEvent:
    """One success, stale failure, or external publication."""

    source_id: str
    snapshot: SourceSnapshot | None
    error: SourceError | None = None
    external: bool = False


@dataclass
class _SourceState:
    next_deadline: float | None = None
    in_flight: bool = False
    dispatching: bool = False
    future: Future[Any] | None = None
    consecutive_failures: int = 0
    last_started: float | None = None
    last_completed: float | None = None
    active: bool = False
    demands: dict[str, SourceDemand] = field(default_factory=dict)


class RefreshScheduler:
    """Condition/deadline scheduler with per-source single-flight execution."""

    def __init__(
        self,
        sources: Any,
        *,
        cache: SourceCache | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_workers: int = 4,
        thread_name: str = "source-refresh-scheduler",
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self._clock = clock
        self._wall_clock = wall_clock
        self.cache = cache or SourceCache(clock=clock, wall_clock=wall_clock)
        self._max_workers = max_workers
        self._thread_name = thread_name
        self._condition = threading.Condition(threading.RLock())
        self._definitions = self._normalize_definitions(sources)
        self._states = {source_id: _SourceState() for source_id in self._definitions}
        self._listeners: list[Callable[[RefreshEvent], None]] = []
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._running = False
        self._stopping = False
        self._started_at: float | None = None

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def start(self) -> bool:
        """Start the deadline loop once; a stopped scheduler can be restarted."""
        with self._condition:
            if self._running:
                return False
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="source-refresh",
            )
            self._running = True
            self._stopping = False
            self._started_at = self._wall_clock()
            self._reset_deadlines_locked(self._clock())
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self._thread_name,
                daemon=False,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float | None = None) -> None:
        """Stop the loop and wait for every submitted getter worker to exit."""
        with self._condition:
            if not self._running and self._executor is None:
                return
            self._running = False
            self._stopping = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            if timeout is not None:
                thread.join(max(0.0, float(timeout)))
            if thread.is_alive():
                thread.join()
        with self._condition:
            executor = self._executor
            self._executor = None
            self._thread = None
        if executor is not None:
            # Getter calls intentionally have no hard timeout.  Waiting here is
            # required so ThreadPoolExecutor's non-daemon workers never leak.
            executor.shutdown(wait=True, cancel_futures=True)
        with self._condition:
            self._stopping = False
            for state in self._states.values():
                state.in_flight = False
                state.dispatching = False
                state.future = None
            self._started_at = None

    close = stop

    def add_listener(self, listener: Callable[[RefreshEvent], None]) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._condition:
            self._listeners.append(listener)

        def remove() -> None:
            with self._condition:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return remove

    subscribe = add_listener

    def set_demand(
        self,
        demand_id: str,
        source_id: str,
        *,
        interval_ms: int | None = None,
        active: bool = False,
    ) -> SourceDemand:
        """Add or replace one demand and wake the source immediately."""
        demand_id = _identifier(demand_id, "demand_id")
        source_id = _identifier(source_id, "source_id")
        if source_id not in self._definitions:
            raise KeyError(source_id)
        if interval_ms is not None:
            _positive_int(interval_ms, "interval_ms")
        if type(active) is not bool:
            raise TypeError("active must be a boolean")
        demand = SourceDemand(source_id, interval_ms, active)
        with self._condition:
            state = self._states[source_id]
            was_empty = not state.demands
            state.demands[demand_id] = demand
            now = self._clock()
            interval_ms = self._effective_interval_ms_locked(source_id)
            if not state.in_flight and interval_ms is not None:
                if was_empty:
                    if state.consecutive_failures and state.last_completed is not None:
                        policy = _policy(self._definitions[source_id])
                        backoff_ms = min(
                            policy.error_backoff_max_ms,
                            policy.error_backoff_initial_ms
                            * (2 ** (state.consecutive_failures - 1)),
                        )
                        state.next_deadline = max(
                            now,
                            state.last_completed + backoff_ms / 1000.0,
                        )
                    else:
                        state.next_deadline = (
                            now + interval_ms / 1000.0
                            if self.cache.get_fresh(source_id, now=now) is not None
                            else now
                        )
                else:
                    baseline = state.last_completed if state.last_completed is not None else now
                    candidate = baseline + interval_ms / 1000.0
                    state.next_deadline = (
                        candidate
                        if state.next_deadline is None
                        else min(state.next_deadline, candidate)
                    )
            self._condition.notify_all()
        return demand

    update_demand = set_demand

    def replace_demands(
        self,
        demand_id_prefix: str,
        demands: Iterable[SourceDemand],
    ) -> tuple[SourceDemand, ...]:
        """Atomically replace all demands whose ids use one prefix."""
        demand_id_prefix = _identifier(demand_id_prefix, "demand_id_prefix")
        normalized = tuple(demands)
        for demand in normalized:
            if not isinstance(demand, SourceDemand):
                raise TypeError("demands must contain SourceDemand values")
            if demand.source_id not in self._definitions:
                raise KeyError(demand.source_id)
            if demand.interval_ms is not None:
                _positive_int(demand.interval_ms, "interval_ms")
        with self._condition:
            for state in self._states.values():
                for key in tuple(state.demands):
                    if key.startswith(demand_id_prefix):
                        del state.demands[key]
            now = self._clock()
            for index, demand in enumerate(normalized):
                self._states[demand.source_id].demands[
                    f"{demand_id_prefix}{index}"
                ] = demand
                self._states[demand.source_id].next_deadline = now
            self._reconcile_paused_locked(now)
            self._condition.notify_all()
        return normalized

    def remove_demand(self, demand_id: str, source_id: str | None = None) -> bool:
        demand_id = _identifier(demand_id, "demand_id")
        with self._condition:
            source_ids = (source_id,) if source_id is not None else tuple(self._states)
            removed = False
            now = self._clock()
            for candidate in source_ids:
                state = self._states.get(candidate)
                if state is None:
                    continue
                removed = state.demands.pop(demand_id, None) is not None or removed
                self._reconcile_source_locked(candidate, now)
            if removed:
                self._condition.notify_all()
            return removed

    def remove_demands_with_prefix(self, demand_id_prefix: str) -> int:
        demand_id_prefix = _identifier(demand_id_prefix, "demand_id_prefix")
        removed = 0
        with self._condition:
            now = self._clock()
            for source_id, state in self._states.items():
                for key in tuple(state.demands):
                    if key.startswith(demand_id_prefix):
                        del state.demands[key]
                        removed += 1
                self._reconcile_source_locked(source_id, now)
            if removed:
                self._condition.notify_all()
        return removed

    def set_source_active(self, source_id: str, active: bool) -> None:
        if type(active) is not bool:
            raise TypeError("active must be a boolean")
        with self._condition:
            state = self._states[source_id]
            state.active = active
            state.next_deadline = self._clock()
            self._condition.notify_all()

    def effective_interval_ms(self, source_id: str) -> int | None:
        with self._condition:
            return self._effective_interval_ms_locked(source_id)

    def run_due(self, *, now: float | None = None, wait: bool = False) -> tuple[Future[Any], ...]:
        """Submit currently due sources; exposed for deterministic fake-clock tests."""
        now = self._clock() if now is None else float(now)
        with self._condition:
            due = self._collect_due_locked(now)
        futures = tuple(self._submit(source_id) for source_id in due)
        if wait:
            for future in futures:
                try:
                    future.result()
                except Exception:
                    # Completion callbacks own error state and backoff.
                    pass
            self.wait_for_idle(due)
        return futures

    tick = run_due

    def refresh_now(self, source_id: str, *, wait: bool = False) -> Future[Any]:
        """Force one source due while preserving per-source single-flight."""
        with self._condition:
            if source_id not in self._definitions:
                raise KeyError(source_id)
            state = self._states[source_id]
            if state.in_flight and state.future is not None:
                future = state.future
            else:
                state.in_flight = True
                state.last_started = self._clock()
                state.next_deadline = None
                future = self._submit(source_id)
        if wait:
            try:
                future.result()
            except Exception:
                pass
            self.wait_for_idle((source_id,))
        return future

    def refresh_if_due(
        self,
        source_id: str,
        *,
        wait: bool = False,
    ) -> Future[Any] | None:
        """Refresh one demanded source only when its normal deadline is due."""
        with self._condition:
            if source_id not in self._definitions:
                raise KeyError(source_id)
            state = self._states[source_id]
            if state.in_flight and state.future is not None:
                future = state.future
            else:
                now = self._clock()
                if self._effective_interval_ms_locked(source_id) is None:
                    return None
                if state.next_deadline is None:
                    state.next_deadline = now
                if state.next_deadline > now + 1e-9:
                    return None
                state.in_flight = True
                state.last_started = now
                state.next_deadline = None
                future = self._submit(source_id)
        if wait:
            try:
                future.result()
            except Exception:
                pass
            self.wait_for_idle((source_id,))
        return future

    def wait_for_idle(self, source_ids: Iterable[str] | None = None) -> None:
        """Wait until completion callbacks have committed all selected source state."""
        selected = set(self._states if source_ids is None else source_ids)
        with self._condition:
            while any(
                self._states[source_id].in_flight
                or self._states[source_id].dispatching
                for source_id in selected
                if source_id in self._states
            ):
                self._condition.wait()

    def publish_external(
        self,
        source_id: str,
        data: Any,
        *,
        sampled_at_ms: int | None = None,
    ) -> SourceSnapshot:
        """Publish push/external data through the same cache and fan-out path."""
        definition = self._definitions[source_id]
        policy = _policy(definition)
        now = self._clock()
        snapshot = self.cache.record_success(
            source_id,
            data,
            cache_ttl_ms=policy.cache_ttl_ms,
            stale_if_error_ms=policy.stale_if_error_ms,
            now=now,
            sampled_at_ms=sampled_at_ms,
        )
        with self._condition:
            state = self._states[source_id]
            state.consecutive_failures = 0
            state.last_completed = now
            interval_ms = self._effective_interval_ms_locked(source_id)
            state.next_deadline = (
                None if interval_ms is None else now + interval_ms / 1000.0
            )
            self._condition.notify_all()
        self._emit(RefreshEvent(source_id, snapshot, external=True))
        return snapshot

    publish = publish_external

    def invalidate(self, source_id: str, *, refresh: bool = True) -> bool:
        invalidated = self.cache.invalidate(source_id)
        if refresh:
            with self._condition:
                if source_id not in self._states:
                    raise KeyError(source_id)
                if self._effective_interval_ms_locked(source_id) is not None:
                    self._states[source_id].next_deadline = self._clock()
                self._condition.notify_all()
        return invalidated

    def wake(self) -> None:
        """Wake the Condition loop after an injected fake clock advances."""
        with self._condition:
            self._condition.notify_all()

    def health(self) -> dict[str, Any]:
        with self._condition:
            return {
                "status": "ok" if self._running and not self._stopping else "stopped",
                "ok": self._running and not self._stopping,
                "running": self._running,
                "started_at": self._started_at,
                "sources": len(self._states),
                "demands": sum(len(state.demands) for state in self._states.values()),
                "in_flight": sum(1 for state in self._states.values() if state.in_flight),
                "failures": {
                    source_id: state.consecutive_failures
                    for source_id, state in self._states.items()
                    if state.consecutive_failures
                },
                "next_deadlines": {
                    source_id: state.next_deadline
                    for source_id, state in self._states.items()
                    if state.next_deadline is not None
                },
                "cache": self.cache.health(),
            }

    def _run_loop(self) -> None:
        while True:
            with self._condition:
                if not self._running:
                    return
                now = self._clock()
                due = self._collect_due_locked(now)
                if not due:
                    timeout = self._wait_timeout_locked(now)
                    self._condition.wait(timeout=timeout)
                    continue
            for source_id in due:
                self._submit(source_id)

    def _collect_due_locked(self, now: float) -> list[str]:
        due: list[str] = []
        for source_id, state in self._states.items():
            if state.in_flight:
                continue
            interval_ms = self._effective_interval_ms_locked(source_id)
            if interval_ms is None:
                state.next_deadline = None
                continue
            if state.next_deadline is None:
                state.next_deadline = now
            if state.next_deadline > now + 1e-9:
                continue
            state.in_flight = True
            state.last_started = now
            state.next_deadline = None
            due.append(source_id)
        return due

    def _submit(self, source_id: str) -> Future[Any]:
        with self._condition:
            executor = self._executor
            if executor is None:
                # Deterministic tests can drive a scheduler without start().
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-refresh-test")
                temporary = True
            else:
                temporary = False
        try:
            future = executor.submit(self._definitions[source_id].getter)
        except Exception as exc:
            self._complete_failure(source_id, exc)
            if temporary:
                executor.shutdown(wait=True, cancel_futures=True)
            raise
        with self._condition:
            self._states[source_id].future = future
        future.add_done_callback(
            lambda completed, sid=source_id, temp=executor if temporary else None: self._completed(
                sid, completed, temp
            )
        )
        return future

    def _completed(
        self,
        source_id: str,
        future: Future[Any],
        temporary_executor: ThreadPoolExecutor | None,
    ) -> None:
        if future.cancelled():
            with self._condition:
                state = self._states[source_id]
                state.in_flight = False
                state.dispatching = False
                state.future = None
                self._condition.notify_all()
            if temporary_executor is not None:
                temporary_executor.shutdown(wait=False, cancel_futures=False)
            return
        try:
            result = future.result()
        except Exception as exc:
            self._complete_failure(source_id, exc)
        else:
            self._complete_success(source_id, result)
        finally:
            if temporary_executor is not None:
                temporary_executor.shutdown(wait=False, cancel_futures=False)

    def _complete_success(self, source_id: str, data: Any) -> None:
        definition = self._definitions[source_id]
        policy = _policy(definition)
        now = self._clock()
        snapshot = self.cache.record_success(
            source_id,
            data,
            cache_ttl_ms=policy.cache_ttl_ms,
            stale_if_error_ms=policy.stale_if_error_ms,
            now=now,
        )
        with self._condition:
            state = self._states[source_id]
            state.in_flight = False
            state.dispatching = True
            state.future = None
            state.consecutive_failures = 0
            state.last_completed = now
            interval_ms = self._effective_interval_ms_locked(source_id)
            state.next_deadline = (
                None if interval_ms is None else now + interval_ms / 1000.0
            )
        try:
            self._emit(RefreshEvent(source_id, snapshot))
        finally:
            with self._condition:
                state.dispatching = False
                self._condition.notify_all()

    def _complete_failure(self, source_id: str, _exception: BaseException) -> None:
        definition = self._definitions[source_id]
        policy = _policy(definition)
        error = SourceError.refresh_failed(source_id)
        now = self._clock()
        stale = self.cache.record_error(
            source_id,
            error,
            stale_if_error_ms=policy.stale_if_error_ms,
            now=now,
        )
        with self._condition:
            state = self._states[source_id]
            state.in_flight = False
            state.dispatching = True
            state.future = None
            state.consecutive_failures += 1
            state.last_completed = now
            backoff_ms = min(
                policy.error_backoff_max_ms,
                policy.error_backoff_initial_ms * (2 ** (state.consecutive_failures - 1)),
            )
            state.next_deadline = (
                now + backoff_ms / 1000.0
                if self._effective_interval_ms_locked(source_id) is not None
                else None
            )
        try:
            self._emit(RefreshEvent(source_id, stale, error=error))
        finally:
            with self._condition:
                state.dispatching = False
                self._condition.notify_all()

    def _emit(self, event: RefreshEvent) -> None:
        with self._condition:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Delivery failures must never poison refresh state or other listeners.
                continue

    def _effective_interval_ms_locked(self, source_id: str) -> int | None:
        policy = _policy(self._definitions[source_id])
        state = self._states[source_id]
        if not state.demands:
            if policy.pause_without_subscribers:
                return None
            requested = policy.active_interval_ms if state.active else policy.default_interval_ms
            return max(policy.minimum_interval_ms, requested)
        requested_intervals = [
            demand.interval_ms
            if demand.interval_ms is not None
            else (
                policy.active_interval_ms
                if demand.active and policy.active_interval_ms is not None
                else policy.default_interval_ms
            )
            for demand in state.demands.values()
        ]
        if state.active and policy.active_interval_ms is not None:
            requested_intervals.append(policy.active_interval_ms)
        return max(policy.minimum_interval_ms, min(requested_intervals))

    def _wait_timeout_locked(self, now: float) -> float | None:
        deadlines = [
            state.next_deadline
            for source_id, state in self._states.items()
            if not state.in_flight
            and self._effective_interval_ms_locked(source_id) is not None
            and state.next_deadline is not None
        ]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - now)

    def _reset_deadlines_locked(self, now: float) -> None:
        for source_id in self._states:
            self._reconcile_source_locked(source_id, now)

    def _reconcile_paused_locked(self, now: float) -> None:
        for source_id in self._states:
            self._reconcile_source_locked(source_id, now)

    def _reconcile_source_locked(self, source_id: str, now: float) -> None:
        state = self._states[source_id]
        interval_ms = self._effective_interval_ms_locked(source_id)
        if interval_ms is None:
            state.next_deadline = None
        elif state.next_deadline is None and not state.in_flight:
            state.next_deadline = now

    @staticmethod
    def _normalize_definitions(sources: Any) -> dict[str, DataSourceDefinition]:
        if isinstance(sources, Mapping):
            values = tuple(sources.values())
        else:
            iterator = getattr(sources, "iter_data_sources", None)
            values = tuple(iterator()) if callable(iterator) else tuple(sources)
        definitions: dict[str, DataSourceDefinition] = {}
        for definition in values:
            source_id = definition.descriptor.id
            if source_id in definitions:
                raise ValueError(f"duplicate data source: {source_id}")
            definitions[source_id] = definition
        return definitions


def _policy(definition: DataSourceDefinition) -> DataSourceRefreshPolicy:
    policy = definition.descriptor.refresh_policy
    if not isinstance(policy, DataSourceRefreshPolicy):
        raise TypeError("data source descriptor has no normalized refresh policy")
    return policy


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")
    return value


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


__all__ = ["RefreshEvent", "RefreshScheduler", "SourceDemand"]
