"""Dashboard 聚合层的稳定类型化契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias, TypedDict

from contracts.provider import ProviderStatus


ProviderSnapshots: TypeAlias = dict[str, dict[str, Any]]


DashboardTotalsPayload = TypedDict(
    "DashboardTotalsPayload",
    {"in": int, "out": int, "cache": int, "total": int, "inMiss": int},
)


@dataclass
class DashboardTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    uncached_input_tokens: int = 0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        total_tokens: int,
        uncached_input_tokens: int,
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached_input_tokens
        self.total_tokens += total_tokens
        self.uncached_input_tokens += uncached_input_tokens

    def to_payload(self) -> DashboardTotalsPayload:
        return {
            "in": self.input_tokens,
            "out": self.output_tokens,
            "cache": self.cached_input_tokens,
            "total": self.total_tokens,
            "inMiss": self.uncached_input_tokens,
        }


@dataclass(frozen=True)
class UsageSource:
    provider: str
    ok: bool
    source_count: int | None = None
    period: str | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": self.provider, "ok": self.ok}
        if self.source_count is not None:
            payload["source_count"] = self.source_count
        if self.period is not None:
            payload["period"] = self.period
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass
class DashboardAggregate:
    success: bool
    timestamp: str
    today: DashboardTotals
    provider_statuses: dict[str, ProviderStatus] = field(default_factory=dict)
    usage_sources: list[UsageSource] = field(default_factory=list)
    snapshots: ProviderSnapshots = field(default_factory=dict)

    def to_public_payload(self) -> dict[str, Any]:
        """序列化公开 Dashboard 数据，不暴露 Provider 快照。"""
        return {
            "success": self.success,
            "timestamp": self.timestamp,
            "today": self.today.to_payload(),
            "provider_statuses": {
                provider: status.to_provider_payload()
                for provider, status in self.provider_statuses.items()
            },
            "usage_sources": [source.to_payload() for source in self.usage_sources],
        }

    def to_compat_payload(self) -> dict[str, Any]:
        """旧服务层兼容形状，额外携带私有 ``_provider_snapshots``。"""
        payload = self.to_public_payload()
        payload["_provider_snapshots"] = {
            provider: dict(values) for provider, values in self.snapshots.items()
        }
        return payload
