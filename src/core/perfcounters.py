"""Windows 高性能计数器采样。

系统监控每两秒刷新一次；这里通过 PDH 和 WMI 直接读取高频指标，
避免每次刷新都启动 PowerShell。模块是可选能力：若 pywin32 缺失、
性能计数器不可用或驱动未发布对应数据，调用方会收到 ``None`` 并可
回退到原有采样方式。

PDH/WMI 句柄按线程持有。Dashboard 同时会在首次同步采集、快照线程和
动态刷新线程中访问系统信息，跨线程复用原生句柄并不可靠。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import threading
import time
from typing import Iterable

logger = logging.getLogger("cuckoo.perfcounters")

try:
    import pythoncom
    import win32com.client
    import win32pdh
except ImportError:  # 允许测试、最小安装和非 Windows 环境安全导入
    pythoncom = None
    win32com = None
    win32pdh = None


_CPU_FREQ_PATH = r"\Processor Information(_Total)\Actual Frequency"
_GPU_ENGINE_PATH = r"\GPU Engine(*)\Utilization Percentage"
_GPU_MEMORY_QUERY = (
    "SELECT Name, DedicatedUsage "
    "FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory"
)
_GPU_COUNTER_REFRESH_SECONDS = 15.0
_GPU_LUID_RE = re.compile(r"luid_0x[0-9a-f]+_0x([0-9a-f]+)", re.IGNORECASE)
_GPU_IGNORED_ENGINE_RE = re.compile(r"(?:copy|video|session)", re.IGNORECASE)


@dataclass
class GpuDynamicMetrics:
    """由 LUID 索引的 GPU 高频指标。"""

    utilization_by_luid: dict[str, int]
    vram_used_by_luid: dict[str, int]


def extract_gpu_luid(value: str | None) -> str | None:
    """从 PDH/WMI 实例名中提取 LUID 的低位十六进制值。"""
    match = _GPU_LUID_RE.search(value or "")
    return match.group(1).lower() if match else None


def aggregate_gpu_engine_samples(
    samples: Iterable[tuple[str, int | float | str | None]],
) -> dict[str, int]:
    """按 LUID 聚合 GPU 引擎利用率，保留非复制/视频引擎的最大值。"""
    totals: dict[str, int] = {}
    for path, raw_value in samples:
        luid = extract_gpu_luid(path)
        if not luid or _GPU_IGNORED_ENGINE_RE.search(path or ""):
            continue
        try:
            value = max(0, int(round(float(raw_value))))
        except (TypeError, ValueError):
            continue
        totals[luid] = max(totals.get(luid, 0), value)
    return totals


def normalize_gpu_memory_rows(
    rows: Iterable[tuple[str, int | float | str | None]],
) -> dict[str, int]:
    """将 GPU Adapter Memory WMI 行规范化为 ``{luid: dedicated_usage}``。"""
    result: dict[str, int] = {}
    for name, raw_value in rows:
        luid = extract_gpu_luid(name)
        if not luid:
            continue
        try:
            value = max(0, int(float(raw_value or 0)))
        except (TypeError, ValueError):
            continue
        # 同一 LUID 若出现重复记录，选择更高的专用显存占用。
        result[luid] = max(result.get(luid, 0), value)
    return result


class WindowsPerformanceSampler:
    """当前线程的 PDH/WMI 采样器。"""

    def __init__(self):
        self._available = all((pythoncom, win32com, win32pdh))
        self._cpu_query = None
        self._cpu_counter = None
        self._gpu_query = None
        self._gpu_counters: dict[str, object] = {}
        self._gpu_counter_refresh_at = 0.0
        self._wmi_service = None
        self._com_initialized = False
        self._warned_unavailable = False

    @property
    def available(self) -> bool:
        return self._available

    def _mark_unavailable(self, message: str, exc: Exception | None = None):
        """关闭当前原生路径，并只记录一次降级原因。"""
        if not self._warned_unavailable:
            if exc is None:
                logger.warning("[perf] %s；将回退到 PowerShell 采样", message)
            else:
                logger.warning("[perf] %s：%s；将回退到 PowerShell 采样", message, exc)
            self._warned_unavailable = True
        self._available = False
        self.close()

    def _ensure_com(self) -> bool:
        if not self._available:
            return False
        if self._com_initialized:
            return True
        try:
            pythoncom.CoInitialize()
            self._com_initialized = True
            return True
        except Exception as exc:
            self._mark_unavailable("初始化 COM 失败", exc)
            return False

    def _ensure_cpu_counter(self) -> bool:
        if not self._available:
            return False
        if self._cpu_counter is not None:
            return True
        query = None
        try:
            query = win32pdh.OpenQuery()
            counter = win32pdh.AddEnglishCounter(query, _CPU_FREQ_PATH)
            # 预热样本；Actual Frequency 在下一次采样后才有稳定格式化值。
            win32pdh.CollectQueryData(query)
            self._cpu_query = query
            self._cpu_counter = counter
            return True
        except Exception as exc:
            if query is not None:
                try:
                    win32pdh.CloseQuery(query)
                except Exception:
                    pass
            self._mark_unavailable("初始化 CPU PDH 计数器失败", exc)
            return False

    def sample_cpu_frequency_mhz(self) -> int | None:
        """读取 CPU 实际频率（MHz）；首个预热周期或失败时返回 ``None``。"""
        if not self._ensure_cpu_counter():
            return None
        try:
            win32pdh.CollectQueryData(self._cpu_query)
            _, value = win32pdh.GetFormattedCounterValue(
                self._cpu_counter, win32pdh.PDH_FMT_LONG,
            )
            return max(0, int(round(value)))
        except Exception:
            # 第一次读取可能只有基线样本；不禁用整个后端，留给下一周期。
            return None

    def _close_gpu_query(self):
        if self._gpu_query is not None:
            try:
                win32pdh.CloseQuery(self._gpu_query)
            except Exception:
                pass
        self._gpu_query = None
        self._gpu_counters = {}

    def _refresh_gpu_counters_if_needed(self) -> bool:
        if not self._available:
            return False
        now = time.monotonic()
        if self._gpu_query is not None and now < self._gpu_counter_refresh_at:
            return True

        try:
            paths = win32pdh.ExpandCounterPath(_GPU_ENGINE_PATH)
            paths = [
                path for path in paths
                if extract_gpu_luid(path) and not _GPU_IGNORED_ENGINE_RE.search(path)
            ]
            if not paths:
                raise RuntimeError("未发现可用 GPU Engine 计数器")

            query = win32pdh.OpenQuery()
            counters: dict[str, object] = {}
            for path in paths:
                try:
                    counters[path] = win32pdh.AddEnglishCounter(query, path)
                except Exception:
                    # 短生命周期进程可能在展开路径和添加计数器之间退出。
                    continue
            if not counters:
                win32pdh.CloseQuery(query)
                raise RuntimeError("未能添加 GPU Engine 计数器")

            # 先采集基线，再在后续 tick 中读取格式化数据。
            win32pdh.CollectQueryData(query)
            self._close_gpu_query()
            self._gpu_query = query
            self._gpu_counters = counters
            self._gpu_counter_refresh_at = now + _GPU_COUNTER_REFRESH_SECONDS
            return True
        except Exception as exc:
            # 保留已有 query 时仍可继续尝试读取，只有完全没有时才降级。
            if self._gpu_query is not None:
                self._gpu_counter_refresh_at = now + _GPU_COUNTER_REFRESH_SECONDS
                return True
            self._mark_unavailable("初始化 GPU PDH 计数器失败", exc)
            return False

    def _sample_gpu_utilization(self) -> dict[str, int] | None:
        if not self._refresh_gpu_counters_if_needed():
            return None
        try:
            win32pdh.CollectQueryData(self._gpu_query)
            samples: list[tuple[str, int | float | str | None]] = []
            for path, counter in self._gpu_counters.items():
                try:
                    _, value = win32pdh.GetFormattedCounterValue(
                        counter, win32pdh.PDH_FMT_LONG,
                    )
                    samples.append((path, value))
                except Exception:
                    # 某个图形进程退出不应影响其它 GPU engine。
                    continue
            if not samples:
                return None
            return aggregate_gpu_engine_samples(samples)
        except Exception:
            return None

    def _get_wmi_service(self):
        if self._wmi_service is not None:
            return self._wmi_service
        if not self._ensure_com():
            return None
        try:
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            self._wmi_service = locator.ConnectServer(".", r"root\cimv2")
            return self._wmi_service
        except Exception as exc:
            self._mark_unavailable("连接 WMI 服务失败", exc)
            return None

    def _sample_gpu_memory(self) -> dict[str, int] | None:
        service = self._get_wmi_service()
        if service is None:
            return None
        try:
            rows = service.ExecQuery(_GPU_MEMORY_QUERY)
            raw_rows = []
            for row in rows:
                raw_rows.append((getattr(row, "Name", ""), getattr(row, "DedicatedUsage", 0)))
            return normalize_gpu_memory_rows(raw_rows)
        except Exception:
            # WMI 服务可能在设备驱动重启后失效，下次重新建立连接。
            self._wmi_service = None
            return None

    def sample_gpu_metrics(self) -> GpuDynamicMetrics | None:
        """读取 GPU 利用率及专用显存占用；任一路失败时返回 ``None``。"""
        if not self._available:
            return None
        utilization = self._sample_gpu_utilization()
        memory = self._sample_gpu_memory()
        if utilization is None or memory is None:
            return None
        return GpuDynamicMetrics(utilization, memory)

    def close(self):
        """释放 PDH 查询句柄；主要供异常降级和测试使用。"""
        self._close_gpu_query()
        if self._cpu_query is not None:
            try:
                win32pdh.CloseQuery(self._cpu_query)
            except Exception:
                pass
        self._cpu_query = None
        self._cpu_counter = None


_thread_local = threading.local()


def _get_thread_sampler() -> WindowsPerformanceSampler:
    sampler = getattr(_thread_local, "sampler", None)
    if sampler is None:
        sampler = WindowsPerformanceSampler()
        _thread_local.sampler = sampler
    return sampler


def get_cpu_actual_frequency_mhz() -> int | None:
    """获取当前线程的 CPU 实际频率采样。"""
    return _get_thread_sampler().sample_cpu_frequency_mhz()


def get_gpu_dynamic_metrics() -> GpuDynamicMetrics | None:
    """获取当前线程的 GPU 动态指标。"""
    return _get_thread_sampler().sample_gpu_metrics()
