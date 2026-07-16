"""Provider 认证生命周期公共模型与自动刷新调度器。"""

from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal, TypeVar

logger = logging.getLogger("cuckoo.providers.auth")

RefreshState = Literal["refreshed", "unchanged", "needs_login", "failed", "skipped"]
RefreshMode = Literal["background", "on_demand", "both"]

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class RefreshResult:
    """Provider 刷新函数的统一返回值。

    ``message`` 和所有状态记录都不得包含密码、Cookie、JWT 或 API token。
    """

    success: bool
    state: RefreshState
    message: str = ""
    expires_at: str | None = None
    changed: bool = False

    @classmethod
    def refreshed(cls, message: str = "", *, expires_at: str | None = None) -> "RefreshResult":
        return cls(True, "refreshed", message, expires_at, True)

    @classmethod
    def unchanged(cls, message: str = "", *, expires_at: str | None = None) -> "RefreshResult":
        return cls(True, "unchanged", message, expires_at, False)

    @classmethod
    def needs_login(cls, message: str = "") -> "RefreshResult":
        return cls(False, "needs_login", message, None, False)

    @classmethod
    def failed(cls, message: str = "") -> "RefreshResult":
        return cls(False, "failed", message, None, False)

    @classmethod
    def skipped(cls, message: str = "") -> "RefreshResult":
        return cls(True, "skipped", message, None, False)


@dataclass(frozen=True)
class AutoRefreshSpec:
    interval_seconds: float
    mode: RefreshMode


@dataclass
class _RefreshRuntime:
    running: bool = False
    last_started_at: float = 0.0
    last_finished_at: float = 0.0
    last_result: RefreshResult | None = None
    next_run_at: float = 0.0


_runtime_lock = threading.RLock()
_runtime: dict[str, _RefreshRuntime] = {}


def _task_key(fn: Callable[..., Any]) -> str:
    provider_id = getattr(fn, "__credential_provider_id__", "")
    return f"{provider_id or fn.__module__}:{fn.__name__}"


def _normalize_result(value: Any) -> RefreshResult:
    if isinstance(value, RefreshResult):
        return value
    if value is None:
        return RefreshResult.unchanged()
    if value is True:
        return RefreshResult.unchanged()
    if value is False:
        return RefreshResult.failed("Provider 刷新函数返回失败")
    raise TypeError("自动刷新函数必须返回 RefreshResult（或兼容的 bool/None）")


def _execute(fn: Callable[..., Any], *, force: bool, args: tuple[Any, ...], kwargs: dict[str, Any]) -> RefreshResult:
    spec: AutoRefreshSpec = getattr(fn, "__auto_refresh_spec__")
    key = _task_key(fn)
    now = time.time()
    with _runtime_lock:
        runtime = _runtime.setdefault(key, _RefreshRuntime())
        if runtime.running:
            return RefreshResult.skipped("刷新任务正在运行")
        should_throttle = spec.mode in {"on_demand", "both"}
        if should_throttle and not force and runtime.last_started_at:
            left = spec.interval_seconds - (now - runtime.last_started_at)
            if left > 0:
                return RefreshResult.skipped(f"距离下次刷新还有 {int(left)} 秒")
        runtime.running = True
        runtime.last_started_at = now
    try:
        result = _normalize_result(fn.__credential_original__(*args, **kwargs))
    except Exception as exc:
        logger.exception("[auth] %s 刷新任务失败", key)
        result = RefreshResult.failed(str(exc))
    finally:
        finished = time.time()
        with _runtime_lock:
            runtime = _runtime.setdefault(key, _RefreshRuntime())
            runtime.running = False
            runtime.last_finished_at = finished
            runtime.last_result = result
            runtime.next_run_at = finished + spec.interval_seconds
    return result


def auto_refresh(*, interval_seconds: float, mode: RefreshMode = "on_demand") -> Callable[[F], F]:
    """标记 Provider 自定义刷新方法。

    - ``background``：由认证调度器周期执行；
    - ``on_demand``：业务代码调用该方法时按间隔执行；
    - ``both``：同时启用两种模式。

    后台任务必须是无需位置参数的函数。所有 Provider 自行决定何时返回
    ``needs_login``、是否处理 401、如何选择活动账户以及如何持久化结果。
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds 必须大于 0")
    if mode not in {"background", "on_demand", "both"}:
        raise ValueError("mode 必须为 background、on_demand 或 both")

    def decorate(fn: F) -> F:
        spec = AutoRefreshSpec(float(interval_seconds), mode)

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> RefreshResult:
            force = bool(kwargs.pop("_credential_force", False))
            return _execute(wrapped, force=force, args=args, kwargs=kwargs)

        setattr(wrapped, "__auto_refresh_spec__", spec)
        setattr(wrapped, "__credential_original__", fn)
        return wrapped  # type: ignore[return-value]

    return decorate


def bind_provider_refreshes(provider_id: str, provider: Any) -> list[Callable[..., Any]]:
    """为已发现 Provider 绑定 ID，并返回可后台调度的刷新函数。"""
    bound: list[Callable[..., Any]] = []
    for value in vars(provider).values():
        if not callable(value) or not hasattr(value, "__auto_refresh_spec__"):
            continue
        setattr(value, "__credential_provider_id__", provider_id)
        spec: AutoRefreshSpec = getattr(value, "__auto_refresh_spec__")
        if spec.mode in {"background", "both"}:
            bound.append(value)
    return bound


def get_refresh_status(provider_id: str | None = None) -> list[dict[str, Any]]:
    """返回脱敏的刷新运行状态，供设置页显示。"""
    prefix = f"{provider_id}:" if provider_id else ""
    with _runtime_lock:
        result: list[dict[str, Any]] = []
        for key, runtime in _runtime.items():
            if prefix and not key.startswith(prefix):
                continue
            payload = {
                "task": key.split(":", 1)[-1],
                "running": runtime.running,
                "last_started_at": runtime.last_started_at or None,
                "last_finished_at": runtime.last_finished_at or None,
                "next_run_at": runtime.next_run_at or None,
                "result": asdict(runtime.last_result) if runtime.last_result else None,
            }
            result.append(payload)
    return sorted(result, key=lambda item: item["task"])


class AuthRefreshScheduler:
    """后台刷新调度器；同一个刷新函数不会重入。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Callable[..., Any]] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def register_provider(self, provider_id: str, provider: Any) -> None:
        with self._lock:
            for fn in bind_provider_refreshes(provider_id, provider):
                self._tasks[_task_key(fn)] = fn

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="provider-auth-refresh")
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.wait(0.5):
            now = time.time()
            with self._lock:
                tasks = list(self._tasks.values())
            for fn in tasks:
                spec: AutoRefreshSpec = getattr(fn, "__auto_refresh_spec__")
                key = _task_key(fn)
                with _runtime_lock:
                    runtime = _runtime.setdefault(key, _RefreshRuntime())
                    due = not runtime.last_finished_at or now >= runtime.next_run_at
                    running = runtime.running
                if due and not running:
                    _execute(fn, force=True, args=(), kwargs={})


refresh_scheduler = AuthRefreshScheduler()
