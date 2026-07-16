"""Provider 插件与调用结果的稳定类型化契约。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, TypedDict, runtime_checkable


T = TypeVar("T")


class ProviderStatusPayload(TypedDict, total=False):
    status: str
    ok: bool
    enabled: bool
    stale: bool
    error: Any
    last_success_at: Any
    details: dict[str, Any]


class DailyUsagePayload(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    total_tokens: int
    source_count: int
    period: str


def _non_negative_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class ProviderStatus:
    """Provider 状态；保留原始字段，同时可投影为标准 health 形状。"""

    status: Any = "unknown"
    ok: bool = False
    enabled: bool = True
    stale: bool = False
    error: Any = None
    last_success_at: Any = None
    details: Any = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    _raw_payload: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_value(
        cls,
        value: Mapping[str, Any] | None,
        *,
        default_status: str = "unknown",
        default_ok: bool | None = None,
        default_enabled: bool = True,
    ) -> "ProviderStatus":
        raw = dict(value) if isinstance(value, Mapping) else {}
        status = raw.get("status", default_status)
        ok = bool(raw.get("ok", status == "ok" if default_ok is None else default_ok))
        enabled = bool(raw.get("enabled", default_enabled))
        stale = bool(raw.get("stale", status == "stale"))
        details = raw.get("details") or {}
        standard_keys = {
            "status", "ok", "enabled", "stale", "error", "last_success_at", "details"
        }
        return cls(
            status=status,
            ok=ok,
            enabled=enabled,
            stale=stale,
            error=raw.get("error"),
            last_success_at=raw.get("last_success_at"),
            details=details,
            extensions={key: item for key, item in raw.items() if key not in standard_keys},
            _raw_payload=raw,
        )

    def to_provider_payload(self) -> ProviderStatusPayload:
        """返回 Provider 原始形状；已有扩展字段和值不会被丢弃或改写。"""
        if self._raw_payload is not None:
            return dict(self._raw_payload)  # type: ignore[return-value]
        payload: ProviderStatusPayload = {
            "status": self.status,
            "ok": self.ok,
            "enabled": self.enabled,
            "error": self.error,
        }
        if self.last_success_at is not None:
            payload["last_success_at"] = self.last_success_at
        payload.update(self.extensions)  # type: ignore[typeddict-item]
        return payload

    def to_health_payload(self) -> ProviderStatusPayload:
        """生成与 health_service 标准化逻辑一致的七键形状。"""
        return {
            "status": self.status,
            "ok": self.ok,
            "enabled": self.enabled,
            "stale": self.stale,
            "error": self.error,
            "last_success_at": self.last_success_at,
            "details": self.details,
        }


@dataclass(frozen=True)
class DailyUsage:
    """归一化的单 Provider 今日用量，不推导或重算 ``total_tokens``。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    total_tokens: int = 0
    source_count: int = 0
    period: str = "today"

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "DailyUsage":
        input_tokens = _non_negative_count(value.get("input_tokens"))
        cached_input_tokens = _non_negative_count(value.get("cached_input_tokens"))
        raw_uncached = value.get("uncached_input_tokens")
        uncached_input_tokens = (
            max(0, input_tokens - cached_input_tokens)
            if "uncached_input_tokens" not in value or raw_uncached is None
            else _non_negative_count(raw_uncached)
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=_non_negative_count(value.get("output_tokens")),
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            total_tokens=_non_negative_count(value.get("total_tokens")),
            source_count=_non_negative_count(value.get("source_count")),
            period=str(value.get("period") or "today"),
        )

    def to_payload(self) -> DailyUsagePayload:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "total_tokens": self.total_tokens,
            "source_count": self.source_count,
            "period": self.period,
        }


@dataclass(frozen=True)
class ProviderCallOutcome(Generic[T]):
    provider: str
    data: T | None = None
    error: str | None = None
    called: bool = True

    @property
    def ok(self) -> bool:
        return self.called and self.error is None

    def to_call_all_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": self.provider, "data": self.data}
        if self.error is not None:
            payload["error"] = self.error
        return payload


@runtime_checkable
class ProviderProtocol(Protocol):
    PROVIDER_ID: str
    CAPABILITIES: Sequence[str]

    def get_status(self) -> Mapping[str, Any]: ...


@runtime_checkable
class DailyUsageCapability(Protocol):
    def get_today_usage(self) -> Mapping[str, Any] | None: ...


@runtime_checkable
class BalanceCapability(Protocol):
    def get_balance(self) -> Mapping[str, Any] | None: ...


@runtime_checkable
class TokenPlanCapability(Protocol):
    def get_plan_detail(self) -> Mapping[str, Any] | None: ...
    def get_plan_usage(self) -> Mapping[str, Any] | None: ...
    def get_daily_detail(self, year: int, month: int) -> Mapping[str, Any] | None: ...
    def get_model_breakdown(self) -> list[Any] | None: ...


@runtime_checkable
class ApiUsageCapability(Protocol):
    def get_usage_summary(self) -> Mapping[str, Any] | None: ...
    def get_channel_breakdown(self, days: int = 7) -> list[Any] | None: ...
