"""System hardware and runtime metrics collection."""

from __future__ import annotations

import copy
import logging
import threading

import json
import os
import platform
import re
import time

import psutil

from core.config import CONFIG_FILE, load_config
from core.perfcounters import get_cpu_actual_frequency_mhz, get_gpu_dynamic_metrics
from core.proc import run_ps

logger = logging.getLogger("cuckoo.system")

_last_success_at: float | None = None
_last_error: str | None = None

# Background snapshot state
_snapshot_data: dict | None = None
_snapshot_lock = threading.Lock()
_snapshot_started = False
_snapshot_thread: threading.Thread | None = None

# Shared lifecycle for both permanent service loops.
_service_thread_lock = threading.RLock()
_service_stop_event = threading.Event()

# Default hardware maps
_DEFAULT_VRAM_MAP = {
    "9070 XT": 45.6 * 1024**3, "9070": 45.6 * 1024**3,
    "7900 XTX": 24 * 1024**3, "7900 XT": 20 * 1024**3,
    "7800 XT": 16 * 1024**3, "7700 XT": 12 * 1024**3,
}
_DEFAULT_APU_DEVS = {"13c0", "13e0", "15bf", "1681", "164f", "15e4"}

# ── Async dynamic refresh state ──
_dynamic_refresh_lock = threading.Lock()
_dynamic_cpu_freq_mhz: int = 0
_dynamic_refresh_started = False
_dynamic_refresh_thread: threading.Thread | None = None


def _get_overrides() -> dict:
    """按配置文件 mtime 缓存硬件覆盖，修改后台配置后自动失效。"""
    try:
        mtime = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else 0
    except OSError:
        mtime = 0
    if getattr(_get_overrides, "_mtime", None) != mtime:
        value = load_config().get("hardware_overrides", {})
        _get_overrides._cache = value if isinstance(value, dict) else {}
        _get_overrides._mtime = mtime
    return getattr(_get_overrides, "_cache", {})


def reload_config() -> None:
    """清理硬件覆盖缓存，并让下一次采样读取最新配置。"""
    global _snapshot_data, _last_success_at, _last_error
    _get_overrides._cache = {}
    _get_overrides._mtime = None
    _snapshot_data = None
    _last_success_at = None
    _last_error = None
    static = getattr(_collect_system_info, "_static", None)
    if isinstance(static, dict):
        static["data"] = None
        static["ts"] = 0


def _get_vram_map() -> dict:
    override = _get_overrides().get("gpu_vram_gb")
    if isinstance(override, dict) and override:
        return {k: float(v) * 1024**3 for k, v in override.items()}
    return _DEFAULT_VRAM_MAP


def _get_apu_devs() -> set:
    override = _get_overrides().get("apu_device_ids")
    if isinstance(override, list) and override:
        return {x.lower() for x in override}
    return _DEFAULT_APU_DEVS


def _get_mem_installed() -> float | None:
    override = _get_overrides().get("mem_installed_gb")
    if isinstance(override, (int, float)) and override > 0:
        return float(override) * 1024**3
    return None


def _get_cpu_model_override() -> str | None:
    override = _get_overrides().get("cpu_model")
    return str(override).strip() if isinstance(override, str) and override.strip() else None


def _get_mem_name_override() -> str | None:
    override = _get_overrides().get("mem_name")
    return str(override).strip() if isinstance(override, str) and override.strip() else None


def _get_gpu_model_override() -> str | None:
    override = _get_overrides().get("gpu_model")
    return str(override).strip() if isinstance(override, str) and override.strip() else None


_SMBIOS_TO_DDR = {20: "DDR2", 21: "DDR2", 22: "DDR2", 24: "DDR4", 25: "DDR4", 26: "DDR5", 34: "DDR5"}


def _smbios_to_ddr_name(smbios_type: int) -> str:
    return _SMBIOS_TO_DDR.get(smbios_type, "DDR")


def _async_dynamic_loop():
    """后台线程：每 2s 异步刷新 GPU 利用率/显存 + 磁盘用量 + CPU实时频率。"""
    global _dynamic_cpu_freq_mhz, _dynamic_refresh_started, _dynamic_refresh_thread
    try:
        while not _service_stop_event.is_set():
            try:
                # 读取静态快照中的 gpus / disks 列表（共享引用，直接原地更新）
                if not hasattr(_collect_system_info, "_static"):
                    if _service_stop_event.wait(2):
                        break
                    continue
                static = _collect_system_info._static
                s = static.get("data")

                # 刷新 GPU/磁盘时先深拷贝、改完再整体替换引用：已发布的快照
                # （Flask/WS 正在序列化的对象）永远不会被原地修改，避免
                # "dictionary changed size during iteration" 崩溃和撕裂数据。
                gpus_copy = copy.deepcopy(s["gpus"]) if s and s.get("gpus") is not None else None
                disks_copy = copy.deepcopy(s["disks"]) if s and s.get("disks") is not None else None

                # ── CPU 实时频率：优先使用常驻 PDH query，缺失时兼容旧 PS 路径 ──
                freq_mhz = get_cpu_actual_frequency_mhz()
                if freq_mhz is None:
                    try:
                        out = run_ps(r"""
$c = Get-Counter '\Processor Information(_Total)\Actual Frequency' -ErrorAction Stop
[math]::Round($c.CounterSamples.CookedValue, 0)
""", timeout=3)
                        freq_mhz = int(out) if out else None
                    except Exception:
                        freq_mhz = None
                if freq_mhz is not None:
                    with _dynamic_refresh_lock:
                        _dynamic_cpu_freq_mhz = freq_mhz

                if gpus_copy is not None:
                    _refresh_dynamic(gpus_copy, disks_copy if disks_copy is not None else [])
                # 磁盘插拔检测（每30秒）
                if disks_copy is not None:
                    _check_disk_changes(disks_copy)
                if gpus_copy is not None:
                    s["gpus"] = gpus_copy
                if disks_copy is not None:
                    s["disks"] = disks_copy
            except Exception as e:
                logger.error(f"[sys] async dynamic refresh error: {e}")
            if _service_stop_event.wait(2):
                break
    finally:
        with _service_thread_lock:
            if _dynamic_refresh_thread is threading.current_thread():
                _dynamic_refresh_thread = None
                _dynamic_refresh_started = False



def _service_threads_alive_locked() -> bool:
    return bool(
        (_snapshot_thread and _snapshot_thread.is_alive())
        or (_dynamic_refresh_thread and _dynamic_refresh_thread.is_alive())
    )



def _ensure_dynamic_thread():
    """懒启动异步动态刷新线程；停止完成后允许再次启动。"""
    global _dynamic_refresh_started, _dynamic_refresh_thread
    with _service_thread_lock:
        if _dynamic_refresh_thread and _dynamic_refresh_thread.is_alive():
            return
        if _service_stop_event.is_set():
            if _service_threads_alive_locked():
                return
            _service_stop_event.clear()
        _dynamic_refresh_started = True
        _dynamic_refresh_thread = threading.Thread(
            target=_async_dynamic_loop,
            daemon=True,
            name="sys-dynamic",
        )
        _dynamic_refresh_thread.start()
    logger.info("[sys] async dynamic refresh thread started (2s interval)")



def _collect_system_info() -> dict:
    """获取系统硬件信息（静态5分钟缓存 + 动态psutil即时采集，GPU/磁盘由异步线程刷新）"""
    now = time.time()

    # ── 静态缓存（5分钟）：CPU型号、内存频率、GPU名称/显存、磁盘型号 ──
    if not hasattr(_collect_system_info, "_static"):
        _collect_system_info._static = {"data": None, "ts": 0}
    static = _collect_system_info._static
    need_static = (static["data"] is None) or (now - static["ts"]) > 300

    if need_static:
        hw = _fetch_static_hardware()
        if hw:
            # GPU: 注册表显存 → VRAM 型号映射兜底 → 手动覆盖
            gpu_model_override = _get_gpu_model_override()
            registry_vram = _detect_gpu_vram(hw.get("GPUs", []))
            vram_map = _get_vram_map()
            apu_devs = _get_apu_devs()
            gpus = []
            for g in hw.get("GPUs", []):
                name = g.get("Name", "")
                pnp = g.get("PNP", "")
                vram = registry_vram.get(pnp.lower(), 0)
                if not vram:
                    vram = g.get("VRAM", 0) or 0
                if not vram:
                    for key, val in vram_map.items():
                        if key.lower() in name.lower():
                            vram = val; break
                dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
                is_discrete = bool(dm and dm.group(1).lower() not in apu_devs)
                # 应用 GPU 型号覆盖（仅独显）
                display_name = gpu_model_override if (gpu_model_override and is_discrete) else name
                gpus.append({
                    "name": display_name, "vram": vram, "util": 0, "pnp": pnp,
                    "vram_used": 0, "is_discrete": is_discrete,
                })

            # 内存名称：DDR型号 + 空格 + 频率
            smbios_type = hw.get("MemSmbiosType", 0) or 0
            mem_freq = hw.get("MemFreq", 0) or 0
            detected_mem_name = f"{_smbios_to_ddr_name(smbios_type)} {mem_freq}".strip()
            if detected_mem_name == "DDR":
                detected_mem_name = hw.get("MemType", "") or "DDR"

            # 磁盘: 计算用量
            disks = []
            for dk in hw.get("Disks", []):
                model = dk.get("Model", "Unknown")
                letters = dk.get("Letters", "")
                if letters:
                    model += f" ({letters})"
                disks.append({"model": model, "total": 0, "used": 0, "percent": 0, "type": dk.get("MediaType", "Unknown")})
            # 初始刷新磁盘用量
            _refresh_dynamic(gpus, disks)

            static["data"] = {
                "cpu_model": _get_cpu_model_override() or hw.get("CpuModel", platform.processor() or "Unknown CPU"),
                "cpu_freq_max": 0,
                "cpu_cores_p": hw.get("CpuCoresPhysical", psutil.cpu_count(logical=False)),
                "cpu_cores_l": hw.get("CpuCores", psutil.cpu_count(logical=True)),
                "mem_freq": mem_freq,
                "mem_type": hw.get("MemType", ""),
                "mem_name": _get_mem_name_override() or detected_mem_name,
                "mem_installed": _get_mem_installed() or psutil.virtual_memory().total,
                "gpus": gpus,
                "disks": disks,
            }
            static["ts"] = now
            logger.info("[sys] static info refreshed (1 PS call)")

    s = static["data"]
    if s is None:
        # 首次 PowerShell 静态采集失败时给出明确错误，而不是让下面的
        # 下标访问抛出误导性的 TypeError('NoneType' is not subscriptable)。
        raise RuntimeError("静态硬件信息采集失败（PowerShell 查询无输出或超时）")

    # ── 动态数据（每次都采集，很快）──
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()

    # 网络速率
    net = psutil.net_io_counters()
    if not hasattr(_collect_system_info, "_net_prev"):
        _collect_system_info._net_prev = {"sent": net.bytes_sent, "recv": net.bytes_recv, "ts": now}
    np = _collect_system_info._net_prev
    dt = now - np["ts"]
    rate_up = max(0, (net.bytes_sent - np["sent"]) / dt) if dt > 0 else 0
    rate_down = max(0, (net.bytes_recv - np["recv"]) / dt) if dt > 0 else 0
    _collect_system_info._net_prev = {"sent": net.bytes_sent, "recv": net.bytes_recv, "ts": now}

    # GPU 利用率 + 磁盘用量 + CPU频率：由 _async_dynamic_loop 后台刷新，这里直接读快照
    with _dynamic_refresh_lock:
        cpu_freq_dynamic = _dynamic_cpu_freq_mhz

    uptime_sec = now - psutil.boot_time()

    data = {
        "cpu": {
            "percent": cpu_percent,
            "cores_physical": s["cpu_cores_p"],
            "cores_logical": s["cpu_cores_l"],
            "freq_current": cpu_freq_dynamic or (round(cpu_freq.current, 0) if cpu_freq else 0),
            "freq_max": s["cpu_freq_max"],
            "model": s["cpu_model"],
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "percent": mem.percent,
            "freq": s["mem_freq"],
            "type": s["mem_type"],
            "name": s.get("mem_name", ""),
            "installed": s.get("mem_installed", 0),
        },
        "gpus": s["gpus"],
        "disks": s["disks"],
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "rate_up": round(rate_up),
            "rate_down": round(rate_down),
        },
        "system": {
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
            "python": platform.python_version(),
            "uptime": int(uptime_sec),
        },
    }

    return data


def get_system_info() -> dict:
    """Return the latest system snapshot (from background thread)."""
    global _last_success_at, _last_error
    with _snapshot_lock:
        data = _snapshot_data
    if data is None:
        # First call: collect synchronously
        try:
            data = _collect_system_info()
            with _snapshot_lock:
                globals()['_snapshot_data'] = data
            _last_success_at = time.time()
            _last_error = None
        except Exception as e:
            _last_error = str(e)
            raise
    _ensure_snapshot_thread()
    _ensure_dynamic_thread()
    return data


def _snapshot_loop():
    """Background thread: collect system metrics every 2 seconds."""
    global _snapshot_data, _last_success_at, _last_error
    global _snapshot_started, _snapshot_thread
    try:
        while not _service_stop_event.is_set():
            try:
                data = _collect_system_info()
                with _snapshot_lock:
                    _snapshot_data = data
                _last_success_at = time.time()
                _last_error = None
            except Exception as e:
                _last_error = str(e)
            if _service_stop_event.wait(2):
                break
    finally:
        with _service_thread_lock:
            if _snapshot_thread is threading.current_thread():
                _snapshot_thread = None
                _snapshot_started = False



def _ensure_snapshot_thread():
    """Lazily start the snapshot thread, including after a clean stop."""
    global _snapshot_started, _snapshot_thread
    with _service_thread_lock:
        if _snapshot_thread and _snapshot_thread.is_alive():
            return
        if _service_stop_event.is_set():
            if _service_threads_alive_locked():
                return
            _service_stop_event.clear()
        _snapshot_started = True
        _snapshot_thread = threading.Thread(
            target=_snapshot_loop,
            daemon=True,
            name="sys-snapshot",
        )
        _snapshot_thread.start()



def stop_system_service(timeout: float = 5) -> None:
    """Stop and join both system collection loops; lazy access may restart them."""
    global _snapshot_started, _dynamic_refresh_started
    global _snapshot_thread, _dynamic_refresh_thread
    _service_stop_event.set()
    with _service_thread_lock:
        threads = [_snapshot_thread, _dynamic_refresh_thread]
    deadline = time.monotonic() + max(0.0, float(timeout))
    for thread in threads:
        if thread is None or thread is threading.current_thread():
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
    with _service_thread_lock:
        if _snapshot_thread is not None and not _snapshot_thread.is_alive():
            _snapshot_thread = None
            _snapshot_started = False
        if _dynamic_refresh_thread is not None and not _dynamic_refresh_thread.is_alive():
            _dynamic_refresh_thread = None
            _dynamic_refresh_started = False



def get_system_status() -> dict:
    """Return system collector status without triggering a collection."""
    if _last_error:
        status = "error"
    elif _last_success_at:
        status = "ok"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "stale": False,
        "error": _last_error,
        "last_success_at": _last_success_at,
        "details": {},
    }


def _detect_gpu_vram(gpu_list: list[dict]) -> dict[str, int]:
    """通过注册表 qwMemorySize 获取各 GPU 的正确显存（字节），返回 {pnp_id_lower: vram_bytes}。"""
    if not gpu_list:
        return {}
    out = run_ps(r"""
$reg = @{}
try {
    $entries = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*" -ErrorAction SilentlyContinue
    foreach ($e in $entries) {
        $mdid = $e.MatchingDeviceId
        $vram = [long]$e.'HardwareInformation.qwMemorySize'
        if ($mdid -and $vram -gt 0) { $reg[$mdid.ToUpper()] = $vram }
    }
} catch {}
$reg | ConvertTo-Json -Compress
""", timeout=5)
    try:
        raw = json.loads(out) if out else {}
    except Exception:
        return {}
    if not raw:
        return {}
    # 匹配：用 PNPDeviceID 的 VEN_xxxx&DEV_xxxxx 前缀与 MatchingDeviceId 比对
    result = {}
    for g in gpu_list:
        pnp = g.get("PNP", "")
        dm = re.search(r"(VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4})", pnp, re.I)
        if not dm:
            continue
        prefix = dm.group(1).upper()
        for reg_key, vram_val in raw.items():
            if prefix in reg_key:
                result[pnp.lower()] = int(vram_val)
                break
    return result


def _fetch_static_hardware() -> dict:
    """一次 PowerShell 调用获取所有静态硬件信息"""
    out = run_ps(r"""
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$mem = Get-CimInstance Win32_PhysicalMemory | Select-Object -First 1
$gpuList = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notlike '*Idd*' -and $_.Name -notlike '*Microsoft*' }
$physDisks = Get-PhysicalDisk

$gpuArr = @()
foreach ($g in $gpuList) { $gpuArr += @{Name=$g.Name; VRAM=[long]$g.AdapterRAM; PNP=$g.PNPDeviceID} }

$diskArr = @()
foreach ($pd in $physDisks) {
    $parts = Get-Partition -DiskNumber $pd.DeviceId -ErrorAction SilentlyContinue | Where-Object { $_.Type -ne 'Reserved' -and $_.Size -gt 0 }
    $letters = ($parts | Where-Object { $_.DriveLetter } | Select-Object -ExpandProperty DriveLetter) -join ''
    $diskArr += @{Model=$pd.FriendlyName; MediaType=[string]$pd.MediaType; Letters=$letters; Size=[long]$pd.Size}
}

@{
    CpuModel = $cpu.Name
    CpuCores = $cpu.NumberOfLogicalProcessors
    CpuCoresPhysical = $cpu.NumberOfCores
    MemFreq = $mem.ConfiguredClockSpeed
    MemType = switch($mem.SMBIOSMemoryType){24{'DDR4'}26{'DDR5'}34{'DDR5'}default{''}}
    MemSmbiosType = [int]$mem.SMBIOSMemoryType
    GPUs = $gpuArr
    Disks = $diskArr
} | ConvertTo-Json -Depth 4
""", timeout=15)
    try:
        return json.loads(out) if out else {}
    except Exception:
        return {}


def _get_target_gpus(gpus: list) -> list:
    """优先返回独显；没有独显时回退为全部 GPU。"""
    apu_devs = _get_apu_devs()
    discrete = []
    for gpu in gpus:
        pnp = gpu.get("pnp", "")
        match = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
        if match and match.group(1).lower() not in apu_devs:
            discrete.append(gpu)
    return discrete or list(gpus)


def _apply_gpu_luid_metrics(
    gpus: list,
    luid_util: dict[str, int],
    luid_vram: dict[str, int] | None = None,
):
    """将 LUID 指标匹配到展示 GPU，并尽可能稳定地复用已有映射。

    PDH/WMI 的 GPU Adapter Memory 使用 LUID，静态硬件信息使用 PCI PNP ID；
    Windows 没有在这两套公开数据中提供直接关联。首次仍按已有策略，以
    显存容量/当前使用量的降序建立映射；后续刷新则复用保存到 GPU dict 的
    ``luid``，避免采样波动导致卡片跳到另一张显卡。
    """
    target_gpus = _get_target_gpus(gpus)
    if not target_gpus:
        return

    vram_by_luid = luid_vram or {}
    known_luids = set(luid_util) | set(vram_by_luid)
    if not known_luids:
        for gpu in target_gpus:
            gpu["util"] = 0
            if luid_vram is not None:
                gpu["vram_used"] = 0
        return

    assigned_luids = set()
    for gpu in target_gpus:
        luid = str(gpu.get("luid", "")).lower()
        if luid in known_luids and luid not in assigned_luids:
            assigned_luids.add(luid)
        else:
            gpu.pop("luid", None)

    unassigned_gpus = [gpu for gpu in target_gpus if "luid" not in gpu]
    candidate_luids = sorted(
        known_luids - assigned_luids,
        key=lambda luid: (vram_by_luid.get(luid, 0), luid_util.get(luid, 0)),
        reverse=True,
    )
    for gpu, luid in zip(
        sorted(unassigned_gpus, key=lambda gpu: -gpu.get("vram", 0)),
        candidate_luids,
    ):
        gpu["luid"] = luid

    for gpu in target_gpus:
        luid = str(gpu.get("luid", "")).lower()
        gpu["util"] = int(luid_util.get(luid, 0))
        if luid_vram is not None:
            gpu["vram_used"] = int(vram_by_luid.get(luid, 0))


def _refresh_gpu_dynamic_from_powershell(gpus: list):
    """兼容回退：pywin32/驱动计数器不可用时保留旧的 GPU 采样路径。"""
    out = run_ps(r"""
# GPU 引擎利用率：聚合每个 LUID 的最大利用率（跳过拷贝/视频引擎）
$engines = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -Property Name, UtilizationPercentage |
    Where-Object { $_.Name -notmatch 'copy|video|session' }
$luids = @{}
foreach ($e in $engines) {
    if ($e.Name -match 'luid_0x[0-9a-f]+_0x([0-9a-f]+)') {
        $id = $Matches[1].ToLower()
        $u = [int]($e.UtilizationPercentage)
        if ($u -gt ($luids[$id] -as [int])) { $luids[$id] = $u }
    }
}
$luids | ConvertTo-Json -Compress
Write-Output '---SPLIT1---'
# GPU 显存占用：Win32_VideoController.CurrentUsage（字节）
$gpus = Get-CimInstance Win32_VideoController -Property PNPDeviceID, CurrentUsage |
    Where-Object { $_.PNPDeviceID -match 'DEV_' -and $_.Name -notmatch 'Microsoft|Idd' }
$memMap = @{}
foreach ($g in $gpus) {
    if ($g.PNPDeviceID -match 'DEV_([0-9A-Fa-f]{4})') {
        $memMap[$Matches[1].ToLower()] = [long]($g.CurrentUsage)
    }
}
$memMap | ConvertTo-Json -Compress
""", timeout=8)
    if not out:
        return

    parts = out.split("---SPLIT1---")
    util_json = parts[0].strip() if parts else ""
    mem_json = parts[1].strip() if len(parts) > 1 else ""

    try:
        raw_util = json.loads(util_json)
        luid_util = {str(key).lower(): int(value) for key, value in (raw_util or {}).items()}
    except Exception:
        luid_util = {}
    _apply_gpu_luid_metrics(gpus, luid_util)

    try:
        raw_memory = json.loads(mem_json)
        dev_vram = {str(key).lower(): int(value) for key, value in (raw_memory or {}).items()}
    except Exception:
        dev_vram = {}
    for gpu in gpus:
        match = re.search(r"DEV_([0-9A-Fa-f]{4})", gpu.get("pnp", ""))
        if match:
            dev_id = match.group(1).lower()
            if dev_id in dev_vram:
                gpu["vram_used"] = dev_vram[dev_id]


def _refresh_disk_usage(disks: list):
    """使用 psutil 刷新物理盘聚合用量与各分区明细。"""
    try:
        for dk in disks:
            letters_str = dk.get("model", "")
            match = re.search(r"\(([A-Z]+)\)", letters_str)
            if not match:
                dk["partitions"] = []
                continue

            total = 0
            used = 0
            parts = []
            for letter in match.group(1):
                try:
                    usage = psutil.disk_usage(f"{letter}:\\")
                    total += usage.total
                    used += usage.used
                    parts.append({
                        "letter": letter,
                        "total": usage.total,
                        "used": usage.used,
                        "percent": round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0,
                    })
                except Exception:
                    pass
            if total > 0:
                dk["total"] = total
                dk["used"] = used
                dk["percent"] = round(used / total * 100, 1)
            dk["partitions"] = parts
    except Exception:
        pass


def _refresh_dynamic(gpus: list, disks: list):
    """刷新 GPU 高频指标与磁盘用量，优先走常驻 PDH/WMI 采样。"""
    metrics = get_gpu_dynamic_metrics()
    if metrics is None:
        _refresh_gpu_dynamic_from_powershell(gpus)
    else:
        _apply_gpu_luid_metrics(
            gpus,
            metrics.utilization_by_luid,
            metrics.vram_used_by_luid,
        )
    _refresh_disk_usage(disks)


def _check_disk_changes(disks: list):
    """每30秒检测一次物理磁盘插拔（WMI 查询，有开销）"""
    now = time.time()
    if not hasattr(_check_disk_changes, "_last_ts"):
        _check_disk_changes._last_ts = 0
    if now - _check_disk_changes._last_ts < 30:
        return
    _check_disk_changes._last_ts = now

    out = run_ps(r"""
$physDisks = Get-PhysicalDisk | ForEach-Object {
    $pd = $_
    $letters = (Get-Partition -DiskNumber $pd.DeviceId -ErrorAction SilentlyContinue |
        Where-Object { $_.DriveLetter } |
        Select-Object -ExpandProperty DriveLetter) -join ''
    @{Model=$pd.FriendlyName; MediaType=[string]$pd.MediaType; Letters=$letters; Size=[long]$pd.Size}
}
$physDisks | ConvertTo-Json -Depth 3
""", timeout=5)
    if not out:
        return
    try:
        disk_data = json.loads(out)
        if isinstance(disk_data, dict):
            disk_data = [disk_data]
        current_keys = {d.get("model", "").split(" (")[0] for d in disks}
        new_keys = {d.get("Model", "") for d in disk_data}
        if current_keys != new_keys:
            logger.info(f"[sys] 磁盘变化检测: {current_keys} -> {new_keys}")
            disks.clear()
            for pd in disk_data:
                total = pd.get("Size", 0) or 0
                model = pd.get("Model", "Unknown")
                letters = pd.get("Letters", "")
                if letters:
                    model += f" ({letters})"
                disks.append({
                    "model": model,
                    "total": total,
                    "used": 0,
                    "percent": 0,
                    "type": pd.get("MediaType", "Unknown"),
                })
    except Exception:
        pass
