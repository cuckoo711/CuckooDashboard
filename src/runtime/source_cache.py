"""Thread-safe last-success cache for scheduled workspace data sources."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from contracts.subscription import SourceError, SourceSnapshot


@dataclass
class _CacheEntry:
    data: Any
    sequence: int
    sampled_monotonic: float
    sampled_at_ms: int
    fresh_until: float
    stale_until: float
    error: SourceError | None = None
    invalidated: bool = False


class SourceCache:
    """Keep deep-copied last-success values and monotonic freshness metadata."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._entries: dict[str, _CacheEntry] = {}
        self._sequences: dict[str, int] = {}
        self._errors: dict[str, SourceError] = {}

    def record_success(
        self,
        source_id: str,
        data: Any,
        *,
        cache_ttl_ms: int,
        stale_if_error_ms: int = 0,
        now: float | None = None,
        sampled_at_ms: int | None = None,
    ) -> SourceSnapshot:
        """Store one successful sample and clear its previous error state."""
        _require_source_id(source_id)
        _require_non_negative_int(cache_ttl_ms, "cache_ttl_ms")
        _require_non_negative_int(stale_if_error_ms, "stale_if_error_ms")
        now = self._clock() if now is None else float(now)
        sampled_at_ms = (
            int(round(self._wall_clock() * 1000))
            if sampled_at_ms is None
            else _require_non_negative_int(sampled_at_ms, "sampled_at_ms")
        )
        with self._lock:
            sequence = self._sequences.get(source_id, 0) + 1
            self._sequences[source_id] = sequence
            entry = _CacheEntry(
                data=deepcopy(data),
                sequence=sequence,
                sampled_monotonic=now,
                sampled_at_ms=sampled_at_ms,
                fresh_until=now + cache_ttl_ms / 1000.0,
                stale_until=now + (cache_ttl_ms + stale_if_error_ms) / 1000.0,
            )
            self._entries[source_id] = entry
            self._errors.pop(source_id, None)
            return self._snapshot_locked(source_id, entry, stale=False)

    put = record_success

    def record_error(
        self,
        source_id: str,
        error: SourceError,
        *,
        stale_if_error_ms: int | None = None,
        now: float | None = None,
    ) -> SourceSnapshot | None:
        """Record a sanitized failure while retaining the last successful value."""
        _require_source_id(source_id)
        if not isinstance(error, SourceError):
            raise TypeError("error must be a SourceError")
        now = self._clock() if now is None else float(now)
        with self._lock:
            self._errors[source_id] = error
            entry = self._entries.get(source_id)
            if entry is None:
                return None
            entry.error = error
            if stale_if_error_ms is not None:
                _require_non_negative_int(stale_if_error_ms, "stale_if_error_ms")
                entry.stale_until = max(
                    entry.stale_until,
                    entry.fresh_until + stale_if_error_ms / 1000.0,
                )
            if now > entry.stale_until:
                return None
            return self._snapshot_locked(source_id, entry, stale=True)

    fail = record_error

    def get(
        self,
        source_id: str,
        *,
        allow_stale: bool = True,
        now: float | None = None,
    ) -> SourceSnapshot | None:
        """Return a defensive snapshot, respecting TTL and stale-if-error bounds."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            entry = self._entries.get(source_id)
            if entry is None:
                return None
            if not entry.invalidated and entry.error is None and now <= entry.fresh_until:
                return self._snapshot_locked(source_id, entry, stale=False)
            if not allow_stale or entry.error is None or now > entry.stale_until:
                return None
            return self._snapshot_locked(source_id, entry, stale=True)

    snapshot = get

    def get_fresh(self, source_id: str, *, now: float | None = None) -> SourceSnapshot | None:
        return self.get(source_id, allow_stale=False, now=now)

    def get_error(self, source_id: str) -> SourceError | None:
        with self._lock:
            return self._errors.get(source_id)

    def sequence(self, source_id: str) -> int:
        with self._lock:
            return self._sequences.get(source_id, 0)

    def invalidate(self, source_id: str) -> bool:
        """Mark a cached value stale without discarding its last-success data."""
        with self._lock:
            entry = self._entries.get(source_id)
            if entry is None:
                return False
            entry.invalidated = True
            return True

    def remove(self, source_id: str) -> bool:
        with self._lock:
            removed = self._entries.pop(source_id, None) is not None
            self._errors.pop(source_id, None)
            return removed

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._errors.clear()

    def health(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "errors": len(self._errors),
                "sequences": len(self._sequences),
            }

    @staticmethod
    def _snapshot_locked(source_id: str, entry: _CacheEntry, *, stale: bool) -> SourceSnapshot:
        return SourceSnapshot(
            source_id=source_id,
            data=deepcopy(entry.data),
            sequence=entry.sequence,
            sampled_at_ms=entry.sampled_at_ms,
            stale=stale,
            error=entry.error,
        )


def _require_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not source_id or source_id != source_id.strip():
        raise ValueError("source_id must be a non-empty trimmed string")
    return source_id


def _require_non_negative_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


__all__ = ["SourceCache"]
