"""Stable JSON-facing contracts for data-source subscriptions and snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


class SubscriptionContractError(ValueError):
    """Stable parse/validation failure suitable for a protocol error response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        subscription_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.subscription_id = subscription_id

    def to_error(self, *, source_id: str | None = None) -> "SourceError":
        return SourceError(
            code=self.code,
            message=str(self),
            source_id=source_id,
            subscription_id=self.subscription_id,
            retryable=False,
            details={} if self.field is None else {"field": self.field},
        )


@dataclass(frozen=True)
class SourceSubscription:
    """One card-level interest in a registered source."""

    id: str
    source_id: str
    interval_ms: int | None = None
    query: Mapping[str, Any] | None = None
    replay: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.id, "subscription id")
        _require_identifier(self.source_id, "source id")
        if self.interval_ms is not None and (
            isinstance(self.interval_ms, bool)
            or not isinstance(self.interval_ms, int)
            or self.interval_ms <= 0
        ):
            raise SubscriptionContractError(
                "invalid_subscription_interval",
                "subscription interval_ms must be a positive integer",
                field="interval_ms",
                subscription_id=self.id,
            )
        if type(self.replay) is not bool:
            raise SubscriptionContractError(
                "invalid_subscription_replay",
                "subscription replay must be a boolean",
                field="replay",
                subscription_id=self.id,
            )
        if self.query is not None:
            if not isinstance(self.query, Mapping):
                raise SubscriptionContractError(
                    "invalid_subscription_query",
                    "subscription query must be an object",
                    field="query",
                    subscription_id=self.id,
                )
            if self.query:
                raise SubscriptionContractError(
                    "source_query_not_supported",
                    "non-empty source queries are not supported",
                    field="query",
                    subscription_id=self.id,
                )
            object.__setattr__(self, "query", {})

    @property
    def subscription_id(self) -> str:
        return self.id

    @property
    def delivery_interval_ms(self) -> int | None:
        return self.interval_ms

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        default_id: str | None = None,
    ) -> "SourceSubscription":
        if not isinstance(payload, Mapping):
            raise SubscriptionContractError(
                "invalid_subscription",
                "subscription must be an object",
            )
        source_id = payload.get(
            "source_id",
            payload.get("source", payload.get("channel")),
        )
        subscription_id = payload.get(
            "id",
            payload.get("subscription_id", payload.get("subscriptionId", default_id)),
        )
        interval_ms = payload.get(
            "interval_ms",
            payload.get("delivery_interval_ms", payload.get("deliveryIntervalMs")),
        )
        try:
            return cls(
                id=str(subscription_id or ""),
                source_id=str(source_id or ""),
                interval_ms=interval_ms,
                query=payload.get("query"),
                replay=payload.get("replay", True),
            )
        except SubscriptionContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise SubscriptionContractError(
                "invalid_subscription",
                "invalid subscription",
                subscription_id=str(subscription_id or "") or None,
            ) from exc

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "interval_ms": self.interval_ms,
            "query": deepcopy(self.query),
            "replay": self.replay,
        }

    to_dict = to_payload


@dataclass(frozen=True)
class SubscriptionRequest:
    """Normalized subscribe request supporting card-level and legacy shapes."""

    subscriptions: tuple[SourceSubscription, ...] = ()
    replace: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "subscriptions", tuple(self.subscriptions))
        if type(self.replace) is not bool:
            raise SubscriptionContractError(
                "invalid_subscription_replace",
                "replace must be a boolean",
                field="replace",
            )
        seen: set[str] = set()
        for item in self.subscriptions:
            if item.id in seen:
                raise SubscriptionContractError(
                    "duplicate_subscription_id",
                    f"duplicate subscription id: {item.id}",
                    field="subscriptions",
                    subscription_id=item.id,
                )
            seen.add(item.id)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SubscriptionRequest":
        if not isinstance(payload, Mapping):
            raise SubscriptionContractError(
                "invalid_subscription_request",
                "subscription request must be an object",
            )
        raw_subscriptions = payload.get("subscriptions")
        if raw_subscriptions is None and "sources" in payload:
            raw_sources = payload.get("sources")
            if isinstance(raw_sources, str):
                raw_sources = [raw_sources]
            if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
                raise SubscriptionContractError(
                    "invalid_subscription_sources",
                    "sources must be an array of source ids",
                    field="sources",
                )
            subscriptions = tuple(
                SourceSubscription(
                    id=f"legacy:{source_id}",
                    source_id=str(source_id or ""),
                )
                for source_id in raw_sources
            )
            return cls(subscriptions=subscriptions, replace=payload.get("replace", True))
        if not isinstance(raw_subscriptions, Sequence) or isinstance(
            raw_subscriptions, (str, bytes)
        ):
            raise SubscriptionContractError(
                "invalid_subscriptions",
                "subscriptions must be an array",
                field="subscriptions",
            )
        subscriptions = tuple(
            SourceSubscription.from_payload(item, default_id=f"subscription:{index}")
            for index, item in enumerate(raw_subscriptions)
        )
        return cls(subscriptions=subscriptions, replace=payload.get("replace", True))

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        return {
            "subscriptions": [item.to_payload() for item in self.subscriptions],
            "replace": self.replace,
        }

    to_dict = to_payload


@dataclass(frozen=True)
class SourceError:
    """Sanitized source or subscription error safe to send to clients."""

    code: str
    message: str
    source_id: str | None = None
    subscription_id: str | None = None
    retryable: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.code, "source error code")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("source error message must be a non-empty string")
        if type(self.retryable) is not bool:
            raise TypeError("source error retryable must be a boolean")
        if not isinstance(self.details, Mapping):
            raise TypeError("source error details must be an object")
        object.__setattr__(self, "details", deepcopy(dict(self.details)))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SourceError":
        if not isinstance(payload, Mapping):
            raise TypeError("source error must be an object")
        return cls(
            code=payload.get("code", "invalid_source_error"),
            message=payload.get("message", "invalid source error"),
            source_id=payload.get("source_id"),
            subscription_id=payload.get("subscription_id"),
            retryable=payload.get("retryable", True),
            details=payload.get("details", {}),
        )

    parse = from_payload

    @classmethod
    def refresh_failed(cls, source_id: str) -> "SourceError":
        """Return a deliberately detail-free getter failure."""
        return cls(
            code="source_refresh_failed",
            message="data source refresh failed",
            source_id=source_id,
            retryable=True,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.source_id is not None:
            payload["source_id"] = self.source_id
        if self.subscription_id is not None:
            payload["subscription_id"] = self.subscription_id
        if self.details:
            payload["details"] = deepcopy(dict(self.details))
        return payload

    to_dict = to_payload


@dataclass(frozen=True)
class SourceSnapshot:
    """Serializable last-success snapshot plus current stale/error state."""

    source_id: str
    data: Any
    sequence: int
    sampled_at_ms: int
    stale: bool = False
    error: SourceError | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.source_id, "source id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("source snapshot sequence must be a positive integer")
        if (
            isinstance(self.sampled_at_ms, bool)
            or not isinstance(self.sampled_at_ms, int)
            or self.sampled_at_ms < 0
        ):
            raise ValueError("source snapshot sampled_at_ms must be a non-negative integer")
        if type(self.stale) is not bool:
            raise TypeError("source snapshot stale must be a boolean")
        object.__setattr__(self, "data", deepcopy(self.data))

    @property
    def updated_at_ms(self) -> int:
        return self.sampled_at_ms

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SourceSnapshot":
        if not isinstance(payload, Mapping):
            raise TypeError("source snapshot must be an object")
        raw_error = payload.get("error")
        return cls(
            source_id=payload.get("source_id", ""),
            data=payload.get("data"),
            sequence=payload.get("sequence", 0),
            sampled_at_ms=payload.get("sampled_at_ms", payload.get("updated_at_ms", 0)),
            stale=payload.get("stale", False),
            error=(
                SourceError.from_payload(raw_error)
                if isinstance(raw_error, Mapping)
                else None
            ),
        )

    parse = from_payload

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "data": deepcopy(self.data),
            "sequence": self.sequence,
            "sampled_at_ms": self.sampled_at_ms,
            "stale": self.stale,
            "error": None if self.error is None else self.error.to_payload(),
        }

    to_dict = to_payload


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        code = "invalid_" + label.replace(" ", "_")
        raise SubscriptionContractError(code, f"{label} must be a non-empty trimmed string")
    return value


__all__ = [
    "SourceError",
    "SourceSnapshot",
    "SourceSubscription",
    "SubscriptionContractError",
    "SubscriptionRequest",
]
