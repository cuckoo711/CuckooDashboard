"""System hardware and runtime metrics collection."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
import time

import psutil

_last_success_at: float | None = None
_last_error: str | None = None


def _collect_system_info() -> dict:
    """获取系统硬件信息（双层缓存：静态5分钟，动态1秒）"""
    now = time.time()

    # ── 动态缓存（1秒）──
    if not hasattr(_collect_system_info, "_dyn_cache"):
        _collect_system_info._dyn_cache = {"data": None, "ts": 0}
    dyn = _collect_system_info._dyn_cache
    if dyn["data"] and (now - dyn["ts"]) < 1:
        return dyn["data"]

    # ── 静态缓存（5分钟）：CPU型号、内存频率、GPU名称/显存、磁盘型号 ──
    if not hasattr(_collect_system_info, "_static"):
        _collect_system_info._static = {"data": None, "ts": 0}
    static = _collect_system_info._static
    need_static = (static["data"] is None) or (now - static["ts"]) > 300

    if need_static:
        hw = _fetch_static_hardware()
        if hw:
            # GPU: 应用 VRAM 型号映射修正
            _VRAM_MAP = {
                "9070 XT": 45.6 * 1024**3, "9070": 45.6 * 1024**3,
                "7900 XTX": 24 * 1024**3, "7900 XT": 20 * 1024**3,
                "7800 XT": 16 * 1024**3, "7700 XT": 12 * 1024**3,
            }
            _APU_DEVS = {"13c0", "13e0", "15bf", "1681", "164f", "15e4"}  # 常见 AMD APU iGPU 设备 ID
            gpus = []
            for g in hw.get("GPUs", []):
                name = g.get("Name", "")
                vram = g.get("VRAM", 0) or 0
                for key, val in _VRAM_MAP.items():
                    if key.lower() in name.lower():
                        vram = val; break
                pnp = g.get("PNP", "")
                dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
                is_discrete = bool(dm and dm.group(1).lower() not in _APU_DEVS)
                gpus.append({
                    "name": name, "vram": vram, "util": 0, "pnp": pnp,
                    "vram_used": 0, "is_discrete": is_discrete,
                })

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
                "cpu_model": hw.get("CpuModel", platform.processor() or "Unknown CPU"),
                "cpu_freq_max": 0,
                "cpu_cores_p": hw.get("CpuCoresPhysical", psutil.cpu_count(logical=False)),
                "cpu_cores_l": hw.get("CpuCores", psutil.cpu_count(logical=True)),
                "mem_freq": hw.get("MemFreq", 0) or 0,
                "mem_type": hw.get("MemType", ""),
                "mem_installed": 78.3 * 1024**3,  # Task Manager 显示的已安装内存
                "gpus": gpus,
                "disks": disks,
            }
            static["ts"] = now
            print("[sys] static info refreshed (1 PS call)", flush=True)

    s = static["data"]

    # ── 动态数据（每次都采集，很快）──
    cpu_percent = psutil.cpu_percent(interval=0.3)
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

    # 刷新 GPU 利用率 + 磁盘用量 + CPU频率（一次 PowerShell 调用）
    _refresh_dynamic(s["gpus"], s["disks"])
    cpu_freq_dynamic = getattr(_refresh_dynamic, "cpu_freq_mhz", 0)

    # 检测磁盘插拔（每30秒一次，独立函数）
    _check_disk_changes(s["disks"])

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

    dyn["data"] = data
    dyn["ts"] = now
    return data


def get_system_info() -> dict:
    """Collect system metrics and record the latest service status."""
    global _last_success_at, _last_error
    try:
        data = _collect_system_info()
        _last_success_at = time.time()
        _last_error = None
        return data
    except Exception as e:
        _last_error = str(e)
        raise


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


def _run_ps(script: str, timeout=8) -> str:
    """执行一次 PowerShell，返回 stdout（使用临时脚本文件避免转义问题）"""
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8")
    try:
        tmp.write(script)
        tmp.close()
        r = subprocess.run(
            [ps_path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp.name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.stdout.strip()
    except Exception:
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _fetch_static_hardware() -> dict:
    """一次 PowerShell 调用获取所有静态硬件信息"""
    out = _run_ps(r"""
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
    GPUs = $gpuArr
    Disks = $diskArr
} | ConvertTo-Json -Depth 4
""", timeout=15)
    try:
        return json.loads(out) if out else {}
    except Exception:
        return {}


def _refresh_dynamic(gpus: list, disks: list):
    """一次 PowerShell 调用刷新 GPU 利用率 + 显存占用 + 磁盘用量 + CPU当前频率"""
    out = _run_ps(r"""
# GPU 利用率 + 显存
$engines = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Select-Object Name, UtilizationPercentage
$adapterMem = Get-Counter "\GPU Adapter Memory(*)\Dedicated Usage" -ErrorAction SilentlyContinue
$engines | ConvertTo-Json -Depth 2
Write-Output '---SPLIT1---'
if ($adapterMem) { $adapterMem.CounterSamples | Select-Object Path, CookedValue | ConvertTo-Json -Depth 2 }
Write-Output '---SPLIT2---'
# CPU 当前频率 = MaxClockSpeed * % Processor Performance / 100
$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue
$maxClock = if ($cpu) { $cpu[0].MaxClockSpeed } else { 0 }
$perf = Get-Counter '\Processor Information(_Total)\% Processor Performance' -ErrorAction SilentlyContinue
if ($perf) { [math]::Round($maxClock * $perf.CounterSamples.CookedValue / 100, 0) } else { $maxClock }
""", timeout=8)
    if not out:
        return

    parts1 = out.split("---SPLIT1---")
    engines_json = parts1[0].strip() if len(parts1) > 0 else ""
    rest = parts1[1] if len(parts1) > 1 else ""
    parts2 = rest.split("---SPLIT2---")
    mem_json = parts2[0].strip()
    cpu_freq_mhz = 0
    if len(parts2) > 1:
        try:
            cpu_freq_mhz = int(float(parts2[1].strip()))
        except Exception:
            pass
    _refresh_dynamic.cpu_freq_mhz = cpu_freq_mhz

    # ── GPU 利用率 ──
    luid_util = {}
    try:
        engines = json.loads(engines_json)
        if isinstance(engines, dict): engines = [engines]
        for e in engines:
            m = re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", e.get("Name", ""))
            if m:
                low = m.group(1).lower()
                u = e.get("UtilizationPercentage") or 0
                luid_util[low] = max(luid_util.get(low, 0), u)
    except Exception:
        pass

    luid_vram = {}
    try:
        mem_data = json.loads(mem_json)
        if isinstance(mem_data, dict): mem_data = [mem_data]
        for d in mem_data:
            m = re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", d.get("Path", ""))
            if m:
                low = m.group(1).lower()
                luid_vram[low] = luid_vram.get(low, 0) + int(d.get("CookedValue", 0))
    except Exception:
        pass

    _APU_DEVS = {"13c0", "13e0", "15bf", "1681", "164f", "15e4"}
    sorted_luids = sorted(luid_vram.items(), key=lambda x: -x[1])
    luid_to_id = {}
    matched = set()

    # 优先用 VRAM counter 匹配（有显存数据更准确）
    for low, vram_bytes in sorted_luids:
        if vram_bytes <= 0: continue
        target = None
        for gpu in gpus:
            if id(gpu) in matched: continue
            pnp = gpu.get("pnp", "")
            dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
            if dm and dm.group(1).lower() not in _APU_DEVS:
                target = gpu; break
        if not target:
            for gpu in gpus:
                if id(gpu) not in matched:
                    target = gpu; break
        if target:
            luid_to_id[low] = (id(target), vram_bytes)
            matched.add(id(target))

    # 回退：VRAM counter 为空时（AMD 常见），用利用率 LUID 直接按顺序匹配
    if not luid_to_id and luid_util:
        sorted_util_luids = sorted(luid_util.items(), key=lambda x: -x[1])
        for low, _ in sorted_util_luids:
            target = None
            for gpu in gpus:
                if id(gpu) in matched: continue
                pnp = gpu.get("pnp", "")
                dm = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
                if dm and dm.group(1).lower() not in _APU_DEVS:
                    target = gpu; break
            if not target:
                for gpu in gpus:
                    if id(gpu) not in matched:
                        target = gpu; break
            if target:
                luid_to_id[low] = (id(target), 0)
                matched.add(id(target))

    for gpu in gpus:
        gpu["util"] = 0
    for low, util in luid_util.items():
        if low in luid_to_id:
            uid, _ = luid_to_id[low]
            for gpu in gpus:
                if id(gpu) == uid:
                    gpu["util"] = max(gpu.get("util", 0), util)
                    break
    for low, (uid, vram_bytes) in luid_to_id.items():
        if vram_bytes > 0:
            for gpu in gpus:
                if id(gpu) == uid:
                    gpu["vram_used"] = vram_bytes
                    break

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

    out = _run_ps(r"""
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
            print(f"[sys] 磁盘变化检测: {current_keys} -> {new_keys}", flush=True)
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
