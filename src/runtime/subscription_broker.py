"""Transport-neutral session/subscription broker for workspace data sources."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from contracts.subscription import (
    SourceError,
    SourceSnapshot,
    SourceSubscription,
    SubscriptionContractError,
    SubscriptionRequest,
)
from runtime.refresh_scheduler import RefreshEvent, RefreshScheduler

_CORE_LEGACY_ORDER = ("dashboard_data", "github", "media", "system")


@dataclass
class _SessionState:
    session: Any
    send: Callable[[dict[str, Any]], Any]
    page: str = "unknown"
    workspace_id: str | None = None
    subscriptions: dict[str, SourceSubscription] = field(default_factory=dict)
    last_delivery: dict[str, float] = field(default_factory=dict)
    send_lock: threading.RLock = field(default_factory=threading.RLock)
    closed: bool = False


class SubscriptionBroker:
    """Aggregate session demand and fan one sampled value out to all subscribers."""

    def __init__(
        self,
        registry: Any,
        scheduler: RefreshScheduler,
        *,
        clock: Callable[[], float] = time.monotonic,
        is_owner_available: Callable[[str], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.scheduler = scheduler
        self.cache = scheduler.cache
        self._clock = clock
        self._is_owner_available = is_owner_available
        self._lock = threading.RLock()
        self._sessions: dict[int, _SessionState] = {}
        self._source_subscriptions: dict[str, set[tuple[int, str]]] = {}
        self._remove_scheduler_listener = scheduler.add_listener(self._on_refresh)
        self._closed = False

    def register_session(
        self,
        session: Any,
        *,
        send: Callable[[dict[str, Any]], Any] | None = None,
        page: str = "unknown",
        workspace_id: str | None = None,
        legacy_all: bool = False,
    ) -> None:
        """Register a transport object using ``send_json`` or an injected callback."""
        sender = send or getattr(session, "send_json", None)
        if not callable(sender):
            raise TypeError("session must expose send_json or provide a send callback")
        key = id(session)
        with self._lock:
            if self._closed:
                raise RuntimeError("subscription broker is closed")
            if key in self._sessions and not self._sessions[key].closed:
                return
            self._sessions[key] = _SessionState(
                session=session,
                send=sender,
                page=str(page or "unknown"),
                workspace_id=workspace_id,
            )
        if legacy_all:
            subscriptions = tuple(
                SourceSubscription(id=f"legacy:{source_id}", source_id=source_id)
                for source_id in self._ordered_source_ids()
            )
            self.replace_subscriptions(session, subscriptions, replace=True, replay=False)

    open_session = register_session

    def report_session(
        self,
        session: Any,
        *,
        page: str,
        workspace_id: str | None = None,
    ) -> bool:
        with self._lock:
            state = self._sessions.get(id(session))
            if state is None or state.closed:
                return False
            state.page = str(page or "unknown")
            state.workspace_id = workspace_id
            return True

    def handle_message(self, session: Any, message: Mapping[str, Any] | str | bytes) -> bool:
        """Handle source subscribe/unsubscribe/init messages without WebSocket coupling."""
        try:
            payload = self._parse_message(message)
            message_type = str(payload.get("type") or "")
            if message_type == "subscribe" and (
                "subscriptions" in payload or "sources" in payload
            ):
                request = SubscriptionRequest.from_payload(payload)
                self.replace_subscriptions(
                    session,
                    request.subscriptions,
                    replace=request.replace,
                    replay=True,
                )
                return True
            if message_type == "unsubscribe":
                self._handle_unsubscribe(session, payload)
                return True
            if message_type == "init":
                self.init_session(session)
                return True
            if message_type == "report":
                return self.report_session(
                    session,
                    page=str(payload.get("page") or "unknown"),
                    workspace_id=(
                        str(payload.get("workspace_id") or "main")
                        if payload.get("page") == "dashboard"
                        else None
                    ),
                )
            return False
        except SubscriptionContractError as exc:
            self._send_error(session, exc.to_error())
            return False
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_error(
                session,
                SourceError(
                    code="invalid_subscription_message",
                    message="invalid subscription message",
                    retryable=False,
                ),
            )
            return False

    handle = handle_message

    def replace_subscriptions(
        self,
        session: Any,
        subscriptions: Sequence[SourceSubscription],
        *,
        replace: bool = True,
        replay: bool = True,
    ) -> tuple[SourceSubscription, ...]:
        """Validate then atomically replace/add card-level source interests."""
        normalized = tuple(subscriptions)
        seen: set[str] = set()
        for subscription in normalized:
            if not isinstance(subscription, SourceSubscription):
                raise TypeError("subscriptions must contain SourceSubscription values")
            if subscription.id in seen:
                raise SubscriptionContractError(
                    "duplicate_subscription_id",
                    f"duplicate subscription id: {subscription.id}",
                    subscription_id=subscription.id,
                )
            seen.add(subscription.id)
            self._validate_source_available(subscription)

        session_key = id(session)
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                raise ValueError("session is not registered")
            previous = dict(state.subscriptions)
            target = {} if replace else dict(previous)
            target.update((item.id, item) for item in normalized)
            self._remove_indexes_locked(session_key, previous)
            state.subscriptions = target
            state.last_delivery = {
                subscription_id: delivered_at
                for subscription_id, delivered_at in state.last_delivery.items()
                if subscription_id in target
            }
            self._add_indexes_locked(session_key, target)

        demand_prefix = self._demand_prefix(session_key)
        self.scheduler.remove_demands_with_prefix(demand_prefix)
        for subscription in target.values():
            self.scheduler.set_demand(
                self._demand_id(session_key, subscription.id),
                subscription.source_id,
                interval_ms=subscription.interval_ms,
            )
        if replay:
            for subscription in normalized:
                if subscription.replay:
                    self.replay_subscription(session, subscription.id)
        return tuple(target.values())

    subscribe = replace_subscriptions

    def unsubscribe(
        self,
        session: Any,
        *,
        subscription_ids: Sequence[str] = (),
        source_ids: Sequence[str] = (),
    ) -> int:
        session_key = id(session)
        subscription_ids = {str(item) for item in subscription_ids}
        source_ids = {str(item) for item in source_ids}
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                return 0
            removed = {
                subscription_id: item
                for subscription_id, item in state.subscriptions.items()
                if subscription_id in subscription_ids or item.source_id in source_ids
            }
            for subscription_id, item in removed.items():
                state.subscriptions.pop(subscription_id, None)
                state.last_delivery.pop(subscription_id, None)
                index = self._source_subscriptions.get(item.source_id)
                if index is not None:
                    index.discard((session_key, subscription_id))
                    if not index:
                        self._source_subscriptions.pop(item.source_id, None)
        for subscription_id, item in removed.items():
            self.scheduler.remove_demand(
                self._demand_id(session_key, subscription_id),
                item.source_id,
            )
        return len(removed)

    def init_session(
        self,
        session: Any,
        *,
        refresh_missing: bool = True,
        wait_for_refresh: bool = False,
    ) -> int:
        """Replay cached values in legacy order and refresh missing values single-flight."""
        session_key = id(session)
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                return 0
            subscriptions = tuple(state.subscriptions.values())
        order = {source_id: index for index, source_id in enumerate(self._ordered_source_ids())}
        subscriptions = tuple(
            sorted(
                subscriptions,
                key=lambda item: (order.get(item.source_id, len(order)), item.source_id, item.id),
            )
        )
        sent = 0
        refreshed: set[str] = set()
        for subscription in subscriptions:
            snapshot = self.cache.get(subscription.source_id, allow_stale=True)
            if snapshot is not None:
                if self._deliver(session_key, subscription.id, snapshot, force=True):
                    sent += 1
            elif refresh_missing and subscription.source_id not in refreshed:
                refreshed.add(subscription.source_id)
                self.scheduler.refresh_if_due(
                    subscription.source_id,
                    wait=wait_for_refresh,
                )
        return sent

    replay = init_session

    def replay_subscription(self, session: Any, subscription_id: str) -> bool:
        session_key = id(session)
        with self._lock:
            state = self._sessions.get(session_key)
            subscription = None if state is None else state.subscriptions.get(subscription_id)
        if subscription is None:
            return False
        snapshot = self.cache.get(subscription.source_id, allow_stale=True)
        if snapshot is None:
            return False
        return self._deliver(session_key, subscription_id, snapshot, force=True)

    def close_session(self, session: Any) -> bool:
        """Idempotently remove a session and all scheduler demand."""
        session_key = id(session)
        with self._lock:
            state = self._sessions.pop(session_key, None)
            if state is None or state.closed:
                return False
            state.closed = True
            subscriptions = dict(state.subscriptions)
            self._remove_indexes_locked(session_key, subscriptions)
        self.scheduler.remove_demands_with_prefix(self._demand_prefix(session_key))
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            sessions = [state.session for state in self._sessions.values()]
            self._closed = True
        for session in sessions:
            self.close_session(session)
        self._remove_scheduler_listener()

    def publish_external(
        self,
        source_id: str,
        data: Any,
        *,
        sampled_at_ms: int | None = None,
    ) -> SourceSnapshot:
        return self.scheduler.publish_external(
            source_id,
            data,
            sampled_at_ms=sampled_at_ms,
        )

    publish = publish_external

    def invalidate(self, source_id: str, *, refresh: bool = True) -> bool:
        return self.scheduler.invalidate(source_id, refresh=refresh)

    def subscriptions_for_session(self, session: Any) -> tuple[SourceSubscription, ...]:
        with self._lock:
            state = self._sessions.get(id(session))
            return () if state is None else tuple(state.subscriptions.values())

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "stopped" if self._closed else "ok",
                "ok": not self._closed,
                "sessions": len(self._sessions),
                "subscriptions": sum(
                    len(state.subscriptions) for state in self._sessions.values()
                ),
                "sources": {
                    source_id: len(subscriptions)
                    for source_id, subscriptions in self._source_subscriptions.items()
                },
            }

    def _on_refresh(self, event: RefreshEvent) -> None:
        with self._lock:
            targets = tuple(self._source_subscriptions.get(event.source_id, ()))
        if event.snapshot is not None:
            for session_key, subscription_id in targets:
                self._deliver(session_key, subscription_id, event.snapshot)
        if event.error is not None and event.snapshot is None:
            for session_key, subscription_id in targets:
                if subscription_id.startswith("legacy:"):
                    continue
                self._send_error_by_key(
                    session_key,
                    SourceError(
                        code=event.error.code,
                        message=event.error.message,
                        source_id=event.source_id,
                        subscription_id=subscription_id,
                        retryable=event.error.retryable,
                    ),
                )

    def _deliver(
        self,
        session_key: int,
        subscription_id: str,
        snapshot: SourceSnapshot,
        *,
        force: bool = False,
    ) -> bool:
        now = self._clock()
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                return False
            subscription = state.subscriptions.get(subscription_id)
            if subscription is None or subscription.source_id != snapshot.source_id:
                return False
            if not self._source_owner_available(snapshot.source_id):
                error = self._owner_unavailable_error(snapshot.source_id, subscription_id)
                sender = state.send
                payload = {"type": "source_error", "error": error.to_payload()}
            else:
                interval_ms = self._delivery_interval_ms(subscription)
                last_delivery = state.last_delivery.get(subscription_id)
                if (
                    not force
                    and last_delivery is not None
                    and now - last_delivery + 1e-9 < interval_ms / 1000.0
                ):
                    return False
                state.last_delivery[subscription_id] = now
                sender = state.send
                payload = self._encode_snapshot(state, subscription, snapshot)
        if self._safe_send(session_key, sender, payload):
            return True
        return False

    def _encode_snapshot(
        self,
        state: _SessionState,
        subscription: SourceSubscription,
        snapshot: SourceSnapshot,
    ) -> dict[str, Any]:
        definition = self.registry.get_data_source(snapshot.source_id)
        message_type = definition.descriptor.legacy_message_type
        data = snapshot.data
        if message_type == "media" and state.page == "dashboard" and isinstance(data, Mapping):
            data = _dashboard_media_payload(data)
        if not subscription.id.startswith("legacy:"):
            error = None if snapshot.error is None else snapshot.error.to_payload()
            return {
                "v": 1,
                "type": "data.snapshot",
                "subscriptionId": subscription.id,
                "channel": snapshot.source_id,
                "sequence": snapshot.sequence,
                "timestamp": datetime.fromtimestamp(
                    snapshot.sampled_at_ms / 1000.0,
                    tz=timezone.utc,
                ).isoformat().replace("+00:00", "Z"),
                "data": data,
                "meta": {
                    "status": "stale" if snapshot.stale else "ok",
                    "stale": snapshot.stale,
                    "sampledAtMs": snapshot.sampled_at_ms,
                    "error": error,
                },
            }
        if message_type:
            return {"type": message_type, "data": data}
        return {
            "type": "workspace_source",
            "source_id": snapshot.source_id,
            "data": data,
        }

    def _delivery_interval_ms(self, subscription: SourceSubscription) -> int:
        definition = self.registry.get_data_source(subscription.source_id)
        policy = definition.descriptor.refresh_policy
        requested = subscription.interval_ms or policy.default_interval_ms
        return max(policy.minimum_interval_ms, requested)

    def _validate_source_available(self, subscription: SourceSubscription) -> None:
        try:
            self.registry.get_data_source(subscription.source_id)
        except KeyError as exc:
            raise SubscriptionContractError(
                "source_not_found",
                f"unknown data source: {subscription.source_id}",
                subscription_id=subscription.id,
            ) from exc
        if not self._source_owner_available(subscription.source_id):
            raise SubscriptionContractError(
                "source_owner_unavailable",
                "data source owner is unavailable",
                subscription_id=subscription.id,
            )

    def _source_owner_available(self, source_id: str) -> bool:
        try:
            owner = self.registry.owner_of_data_source(source_id)
        except KeyError:
            return False
        if self._is_owner_available is None:
            return True
        try:
            return bool(self._is_owner_available(owner))
        except Exception:
            return False

    def _owner_unavailable_error(
        self,
        source_id: str,
        subscription_id: str,
    ) -> SourceError:
        return SourceError(
            code="source_owner_unavailable",
            message="data source owner is unavailable",
            source_id=source_id,
            subscription_id=subscription_id,
            retryable=True,
        )

    def _handle_unsubscribe(self, session: Any, payload: Mapping[str, Any]) -> None:
        source_ids = payload.get("sources", ())
        if isinstance(source_ids, str):
            source_ids = (source_ids,)
        subscription_ids = payload.get("subscription_ids", payload.get("ids", ()))
        if isinstance(subscription_ids, str):
            subscription_ids = (subscription_ids,)
        raw_subscriptions = payload.get("subscriptions", ())
        if raw_subscriptions:
            if not isinstance(raw_subscriptions, Sequence) or isinstance(
                raw_subscriptions, (str, bytes)
            ):
                raise SubscriptionContractError(
                    "invalid_subscriptions",
                    "subscriptions must be an array",
                )
            subscription_ids = tuple(subscription_ids) + tuple(
                str(item.get("id") or item.get("subscription_id") or "")
                for item in raw_subscriptions
                if isinstance(item, Mapping)
            )
        self.unsubscribe(
            session,
            subscription_ids=tuple(subscription_ids),
            source_ids=tuple(source_ids),
        )

    def _send_error(self, session: Any, error: SourceError) -> bool:
        return self._send_error_by_key(id(session), error)

    def _send_error_by_key(self, session_key: int, error: SourceError) -> bool:
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                return False
            sender = state.send
        return self._safe_send(
            session_key,
            sender,
            {"type": "source_error", "error": error.to_payload()},
        )

    def _safe_send(
        self,
        session_key: int,
        sender: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
    ) -> bool:
        with self._lock:
            state = self._sessions.get(session_key)
            if state is None or state.closed:
                return False
            send_lock = state.send_lock
        try:
            with send_lock:
                sent = sender(payload)
            if sent is False:
                raise OSError("session send failed")
            return True
        except Exception:
            with self._lock:
                state = self._sessions.get(session_key)
                session = None if state is None else state.session
            if session is not None:
                self.close_session(session)
            return False

    def _add_indexes_locked(
        self,
        session_key: int,
        subscriptions: Mapping[str, SourceSubscription],
    ) -> None:
        for subscription_id, item in subscriptions.items():
            self._source_subscriptions.setdefault(item.source_id, set()).add(
                (session_key, subscription_id)
            )

    def _remove_indexes_locked(
        self,
        session_key: int,
        subscriptions: Mapping[str, SourceSubscription],
    ) -> None:
        for subscription_id, item in subscriptions.items():
            index = self._source_subscriptions.get(item.source_id)
            if index is None:
                continue
            index.discard((session_key, subscription_id))
            if not index:
                self._source_subscriptions.pop(item.source_id, None)

    def _ordered_source_ids(self) -> tuple[str, ...]:
        definitions = tuple(self.registry.iter_data_sources())
        legacy_order = {value: index for index, value in enumerate(_CORE_LEGACY_ORDER)}
        return tuple(
            definition.descriptor.id
            for definition in sorted(
                definitions,
                key=lambda item: (
                    legacy_order.get(
                        item.descriptor.legacy_message_type,
                        len(legacy_order),
                    ),
                    item.descriptor.id,
                ),
            )
        )

    @staticmethod
    def _parse_message(message: Mapping[str, Any] | str | bytes) -> Mapping[str, Any]:
        if isinstance(message, (str, bytes)):
            message = json.loads(message)
        if not isinstance(message, Mapping):
            raise TypeError("message must be an object")
        return message

    @staticmethod
    def _demand_prefix(session_key: int) -> str:
        return f"session:{session_key}:"

    @classmethod
    def _demand_id(cls, session_key: int, subscription_id: str) -> str:
        return f"{cls._demand_prefix(session_key)}{subscription_id}"


def _dashboard_media_payload(frame: Mapping[str, Any]) -> dict[str, Any]:
    keep = {
        "status",
        "title",
        "artist",
        "album",
        "song_id",
        "lyric",
        "next_lyric",
        "lyric_index",
        "next_lyric_index",
        "position",
        "position_effective",
        "duration",
        "progress_ratio",
        "position_source",
        "lyric_offset",
        "lyric_start",
        "lyric_end",
        "lyric_duration",
        "lyric_elapsed",
        "lyric_scroll",
        "lyric_line_progress",
        "server_ts",
    }
    slim = {key: frame.get(key) for key in keep if key in frame}
    slim["media_slim"] = True
    slim["lyrics"] = []
    slim["lyrics_yrc"] = []
    return slim


__all__ = ["SubscriptionBroker"]
