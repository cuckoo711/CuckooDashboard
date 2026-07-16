"""认证刷新装饰器和后台调度器测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from providers.auth import AuthRefreshScheduler, RefreshResult, auto_refresh


def test_on_demand_refresh_respects_interval_and_force_flag():
    calls: list[int] = []

    @auto_refresh(interval_seconds=60, mode="on_demand")
    def refresh():
        calls.append(1)
        return RefreshResult.refreshed("ok")

    assert refresh().state == "refreshed"
    assert refresh().state == "skipped"
    assert refresh(_credential_force=True).state == "refreshed"
    assert len(calls) == 2


def test_background_scheduler_runs_registered_provider_task():
    called = threading.Event()

    @auto_refresh(interval_seconds=0.05, mode="background")
    def refresh():
        called.set()
        return RefreshResult.unchanged("ok")

    provider = SimpleNamespace(refresh_credentials=refresh)
    scheduler = AuthRefreshScheduler()
    scheduler.register_provider("test_provider", provider)
    scheduler.start()
    try:
        assert called.wait(1.0)
    finally:
        scheduler.stop()


def test_refresh_result_is_not_reentrant():
    entered = threading.Event()
    release = threading.Event()

    @auto_refresh(interval_seconds=1, mode="on_demand")
    def refresh():
        entered.set()
        release.wait(1.0)
        return RefreshResult.unchanged()

    worker = threading.Thread(target=lambda: refresh(_credential_force=True), daemon=True)
    worker.start()
    assert entered.wait(1.0)
    assert refresh(_credential_force=True).state == "skipped"
    release.set()
    worker.join(1.0)
