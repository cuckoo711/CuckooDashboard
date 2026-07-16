"""Settings API 的关键稳定结构；Provider 专属值保持动态。"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from contracts.provider import ProviderStatusPayload


class SecretView(TypedDict):
    configured: bool
    masked: str


class ProviderPanel(TypedDict):
    provider: str
    config_key: str
    title: str
    description: str
    order: int
    fields: list[dict[str, Any]]
    status_only_auth: bool
    values: dict[str, Any]
    status: NotRequired[ProviderStatusPayload]
    auth: NotRequired[dict[str, Any]]
    auth_descriptor: NotRequired[dict[str, Any]]


class SettingsOptions(TypedDict):
    ring_providers: list[str]
    model_bar_providers: list[str]
    balance_providers: list[str]
    themes: list[str]
    fonts: list[Any]
    capture_devices: list[Any]


class SettingsPayload(TypedDict):
    config: dict[str, Any]
    providers: list[ProviderPanel]
    options: SettingsOptions
    credential_revision: int | None


class SettingsSaveRequest(TypedDict, total=False):
    config: dict[str, Any]
    secrets: dict[str, Any]
    credential_revision: int


class RuntimeApplyResult(TypedDict):
    applied: list[str]
    errors: list[str]


class SettingsSaveResult(SettingsPayload):
    ok: bool
    applied: list[str]
    errors: list[str]
