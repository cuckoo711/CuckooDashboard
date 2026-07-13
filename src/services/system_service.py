"""System hardware and runtime metrics collection."""

from __future__ import annotations

import logging
import threading

import json
import os
import platform
import re
import time

import psutil

from core.config import load_config
from core.proc import run_ps

logger = logging.getLogger("cuckoo.system")

_last_success_at: float | None = None
_last_error: str | None = None

# Background snapshot state
_snapshot_data: dict | None = None
_snapshot_lock = threading.Lock()
_snapshot_started = False

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


def _get_overrides() -> dict:
    """Read hardware overrides from config.json (cached per import)."""
    if not hasattr(_get_overrides, "_cache"):
        _get_overrides._cache = load_config().get("hardware_overrides", {})
    return _get_overrides._cache


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
    """后台线程：每 2s 异步刷新 GPU 利用率/显存 + 磁盘用量 + CPU实时频率"""
    global _dynamic_cpu_freq_mhz
    while True:
        try:
            # 读取静态快照中的 gpus / disks 列表（共享引用，直接原地更新）
            if not hasattr(_collect_system_info, "_static"):
                time.sleep(2); continue
            static = _collect_system_info._static
            s = static.get("data")

            # ── CPU 实时频率：Get-Counter 比 psutil.cpu_freq() 准确 ──
            try:
                out = run_ps(r"""
$c = Get-Counter '\Processor Information(_Total)\Actual Frequency' -ErrorAction Stop
[math]::Round($c.CounterSamples.CookedValue, 0)
""", timeout=3)
                if out:
                    with _dynamic_refresh_lock:
                        _dynamic_cpu_freq_mhz = int(out)
            except Exception:
                pass

            if s and s.get("gpus") is not None:
                _refresh_dynamic(s["gpus"], s["disks"])
            # 磁盘插拔检测（每30秒）
            if s and s.get("disks") is not None:
                _check_disk_changes(s["disks"])
        except Exception as e:
            logger.error(f"[sys] async dynamic refresh error: {e}")
        time.sleep(2)


def _ensure_dynamic_thread():
    """启动异步动态刷新线程（仅一次）"""
    global _dynamic_refresh_started
    if _dynamic_refresh_started:
        return
    _dynamic_refresh_started = True
    t = threading.Thread(target=_async_dynamic_loop, daemon=True, name="sys-dynamic")
    t.start()
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
    while True:
        try:
            data = _collect_system_info()
            with _snapshot_lock:
                _snapshot_data = data
            _last_success_at = time.time()
            _last_error = None
        except Exception as e:
            _last_error = str(e)
        time.sleep(2)


def _ensure_snapshot_thread():
    """Start the background snapshot thread if not already running."""
    global _snapshot_started
    if _snapshot_started:
        return
    _snapshot_started = True
    t = threading.Thread(target=_snapshot_loop, daemon=True, name="sys-snapshot")
    t.start()


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


def _refresh_dynamic(gpus: list, disks: list):
    """一次 PowerShell 调用刷新 GPU 利用率 + 显存占用"""

    # ── GPU 利用率 + 显存：仅 WMI 查询，不用 Get-Counter（避免通配符展开的 1-3 秒开销）──
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
# GPU 显存占用：Win32_VideoController.CurrentUsage（字节），排除核显
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

    parts1 = out.split("---SPLIT1---")
    util_json = parts1[0].strip() if parts1 else ""
    mem_json = parts1[1].strip() if len(parts1) > 1 else ""

    # ── GPU 利用率（已由 PS 聚合为 {luid_suffix: max_util}）──
    luid_util = {}
    try:
        luid_util = json.loads(util_json)
        luid_util = {k: int(v) for k, v in luid_util.items()}
    except Exception:
        pass

    # ── GPU 显存（Win32_VideoController.CurrentUsage，按 PCI DEV_ID 索引）──
    dev_vram = {}
    try:
        dev_vram = json.loads(mem_json)
        dev_vram = {k: int(v) for k, v in dev_vram.items()}
    except Exception:
        pass

    _APU_DEVS = {"13c0", "13e0", "15bf", "1681", "164f", "15e4"}

    # ── 匹配：GPU engine LUID 和 Win32_VideoController PCI DEV_ID 是不同的标识符，
    #    无法直接对应。策略：分别按 VRAM 大小排序，用位置索引匹配。──

    # 收集独显列表（排除 APU）
    discrete = []
    for gpu in gpus:
        pnp = gpu.get("pnp", "")
        dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
        if dm and dm.group(1).lower() not in _APU_DEVS:
            discrete.append(gpu)
    if not discrete:
        discrete = list(gpus)

    # 将 LUID 利用率按值降序排列 → 匹配独显（按 VRAM 降序排列）
    sorted_util = sorted(luid_util.items(), key=lambda x: -x[1])
    sorted_discrete = sorted(discrete, key=lambda g: -g.get("vram", 0))
    for i, gpu in enumerate(sorted_discrete):
        gpu["util"] = int(sorted_util[i][1]) if i < len(sorted_util) else 0

    # 显存占用：按 DEV_ID 匹配独显
    for gpu in gpus:
        pnp = gpu.get("pnp", "")
        dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
        if dm:
            dev_id = dm.group(1).lower()
            if dev_id in dev_vram:
                gpu["vram_used"] = dev_vram[dev_id]

    # ── 磁盘用量 + 分区明细（psutil 直接读取）──
    try:
        for dk in disks:
            letters_str = dk.get("model", "")
            m = re.search(r"\(([A-Z]+)\)", letters_str)
            if m:
                total = 0
                used = 0
                parts = []
                for letter in m.group(1):
                    try:
                        u = psutil.disk_usage(f"{letter}:\\")
                        total += u.total
                        used += u.used
                        parts.append({"letter": letter, "total": u.total, "used": u.used,
                                       "percent": round(u.used / u.total * 100, 1) if u.total > 0 else 0})
                    except Exception:
                        pass
                if total > 0:
                    dk["total"] = total
                    dk["used"] = used
                    dk["percent"] = round(used / total * 100, 1)
                dk["partitions"] = parts
            else:
                dk["partitions"] = []
    except Exception:
        pass


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
