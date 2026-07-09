#!/usr/bin/env python3
"""
MiMo Usage Dashboard
Web 看板服务器，用于副屏显示 MiMo 使用情况。

使用方式:
    python dashboard.py              # 默认端口 5000
    python dashboard.py --port 8080  # 指定端口
    python dashboard.py --open       # 自动打开浏览器
"""

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

import psutil

try:
    from flask import Flask, jsonify, send_from_directory
except ImportError:
    print("错误: 缺少 Flask 库，请运行: pip install flask")
    sys.exit(1)

# 从 mimo_usage.py 导入 MiMoAPI
try:
    from mimo_usage import MiMoAPI, load_cookies
except ImportError:
    print("错误: 无法导入 mimo_usage.py，请确保文件存在")
    sys.exit(1)

import requests as _requests

app = Flask(__name__, static_folder="static")

# 配置
GITHUB_USER = "cuckoo711"

# 全局缓存
_cache = {
    "data": None,
    "timestamp": 0,
}
_github_cache = {
    "data": None,
    "timestamp": 0,
}
CACHE_TTL = 55  # 缓存55秒（前端60秒刷新）
GITHUB_CACHE_TTL = 600  # GitHub 缓存10分钟
CONFIG_FILE = Path(__file__).parent / "config.json"


# ============================================================
# 本地 MiMo 平台 API 客户端
# ============================================================

LOCAL_TOKEN_CACHE = Path(__file__).parent / "local_tokens.json"


class LocalMimoAPI:
    """本地 MiMo 平台 API 客户端（JWT 认证，token 持久化到磁盘）"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token = ""
        self._token_ts = 0  # token 获取时间
        # 内网 HTTPS 自签证书跳过验证
        self._verify = not (
            self.base_url.startswith("https://") and any(
                host in self.base_url for host in ["192.168.", "10.", "172."]
            )
        )

    @property
    def _cache_key(self) -> str:
        return self.base_url

    def _load_cached_token(self) -> bool:
        """从磁盘缓存恢复 token"""
        if not LOCAL_TOKEN_CACHE.exists():
            return False
        try:
            cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
            entry = cache.get(self._cache_key)
            if entry and entry.get("token"):
                self._token = entry["token"]
                self._token_ts = entry.get("ts", 0)
                # 检查是否还在有效期内（5天）
                if (time.time() - self._token_ts) < 5 * 86400:
                    return True
                self._token = ""
                self._token_ts = 0
        except (json.JSONDecodeError, OSError):
            pass
        return False

    def _save_cached_token(self):
        """将 token 持久化到磁盘"""
        cache = {}
        if LOCAL_TOKEN_CACHE.exists():
            try:
                cache = json.loads(LOCAL_TOKEN_CACHE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cache = {}
        cache[self._cache_key] = {"token": self._token, "ts": self._token_ts}
        try:
            LOCAL_TOKEN_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _login(self) -> bool:
        """登录获取 JWT Token"""
        try:
            resp = _requests.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=8, verify=self._verify,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("token", "")
                self._token_ts = time.time()
                if self._token:
                    self._save_cached_token()
                    return True
            print(f"[local] 登录失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[local] 登录异常 {self.base_url}: {e}", flush=True)
        return False

    def _ensure_token(self) -> bool:
        """确保 token 有效（5天刷新，磁盘缓存）"""
        if self._token and (time.time() - self._token_ts) < 5 * 86400:
            return True
        if self._load_cached_token():
            return True
        return self._login()

    def get_today_usage(self) -> dict | None:
        """获取今日使用量（timeseries 取今天的点）"""
        if not self._ensure_token():
            return None
        try:
            resp = _requests.get(
                f"{self.base_url}/api/usage-history/timeseries",
                params={"granularity": "day"},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10, verify=self._verify,
            )
            if resp.status_code != 200:
                print(f"[local] 获取数据失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
                return None
            data = resp.json()
            points = data.get("points", [])
            if not points:
                return None
            # MiMo 按 UTC 0 点重置 = 北京时间 8:00
            # 早 8 点前（北京时间）用昨天 UTC 日期，8 点后用今天
            from datetime import timezone, timedelta
            bj_now = datetime.now(timezone(timedelta(hours=8)))
            ref = datetime.utcnow() if bj_now.hour >= 8 else datetime.utcnow() - timedelta(days=1)
            target_str = ref.strftime("%Y-%m-%d")
            for p in points:
                ts = p.get("timestamp", "")
                if ts.startswith(target_str) and (p.get("requestCount") or 0) > 0:
                    return p
            return None
        except Exception as e:
            print(f"[local] 获取数据异常 {self.base_url}: {e}", flush=True)
            return None


def load_config() -> dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


_local_apis: list[LocalMimoAPI] | None = None


def get_local_apis() -> list[LocalMimoAPI]:
    """获取所有已配置且可达的本地平台 API 实例（单例，启动时探测）"""
    global _local_apis
    if _local_apis is not None:
        return _local_apis
    _local_apis = []
    config = load_config()
    lp = config.get("local_platforms", {})
    if not lp.get("enabled"):
        return _local_apis
    username = lp.get("username", "")
    default_password = lp.get("password", "")
    urls = lp.get("urls", [])
    if not all([username, default_password, urls]):
        return _local_apis
    for entry in urls:
        if isinstance(entry, dict):
            url = entry.get("url", "")
            pwd = entry.get("password", default_password)
        else:
            url = entry
            pwd = default_password
        if url:
            _local_apis.append(LocalMimoAPI(url, username, pwd))
    print(f"[local] 已配置 {len(_local_apis)} 个本地平台", flush=True)
    return _local_apis


# ============================================================
# NUG 平台 API 客户端
# ============================================================


class NUGApi:
    """NUG (NarraFork) 平台 API 客户端（session cookie 认证）"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = _requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Dashboard",
            "Content-Type": "application/json",
        })
        self._logged_in = False

    def _login(self) -> bool:
        """登录获取 session cookie"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=10,
            )
            if resp.status_code == 200:
                self._logged_in = True
                print(f"[nug] 登录成功: {self.base_url}", flush=True)
                return True
            print(f"[nug] 登录失败 {self.base_url}: HTTP {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[nug] 登录异常 {self.base_url}: {e}", flush=True)
        return False

    def get_data(self) -> dict | None:
        """获取余额"""
        if not self._logged_in:
            if not self._login():
                return None
        try:
            # 获取余额
            me_resp = self.session.get(
                f"{self.base_url}/api/auth/me", timeout=10,
            )
            if me_resp.status_code == 401:
                # session 过期，重新登录
                if self._login():
                    me_resp = self.session.get(
                        f"{self.base_url}/api/auth/me", timeout=10,
                    )
                else:
                    return None
            if me_resp.status_code != 200:
                print(f"[nug] 获取用户信息失败 {self.base_url}: HTTP {me_resp.status_code}", flush=True)
                return None
            me = me_resp.json()
            balance = me.get("quotaBalance", 0)

            return {
                "balance": balance,
            }
        except Exception as e:
            print(f"[nug] 获取数据异常 {self.base_url}: {e}", flush=True)
            return None


_nug_api: NUGApi | None = None


def get_nug_api() -> NUGApi | None:
    """获取 NUG API 实例（单例）"""
    global _nug_api
    if _nug_api is not None:
        return _nug_api
    config = load_config()
    nug = config.get("nug", {})
    if not nug.get("enabled"):
        return None
    url = nug.get("url", "")
    username = nug.get("username", "")
    password = nug.get("password", "")
    if not all([url, username, password]):
        return None
    _nug_api = NUGApi(url, username, password)
    return _nug_api


def get_mimo_api() -> MiMoAPI:
    """获取 MiMoAPI 实例"""
    cache_info = load_cookies()
    cookie_str = cache_info.get("cookie")
    if not cookie_str:
        raise ValueError("未找到 Cookie，请先运行 mimo_usage.py 登录")
    return MiMoAPI(cookie_str)


GITHUB_DISK_CACHE = Path(__file__).parent / "github_cache.json"


def fetch_github_contributions() -> dict:
    """从 GitHub profile 页面抓取完整一年的贡献日历数据，带磁盘缓存和重试"""
    now = time.time()
    if _github_cache["data"] and (now - _github_cache["timestamp"]) < GITHUB_CACHE_TTL:
        return _github_cache["data"]

    # 尝试从磁盘缓存恢复
    if GITHUB_DISK_CACHE.exists():
        try:
            disk = json.loads(GITHUB_DISK_CACHE.read_text(encoding="utf-8"))
            if disk.get("data") and (now - disk.get("ts", 0)) < 86400:  # 1天有效
                _github_cache["data"] = disk["data"]
                _github_cache["timestamp"] = now
                print(f"GitHub: 从磁盘缓存恢复 {len(disk['data'])} 天数据", flush=True)
                return disk["data"]
        except (json.JSONDecodeError, OSError):
            pass

    import re

    def _do_fetch() -> dict:
        resp = _requests.get(
            f"https://github.com/{GITHUB_USER}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise Exception(f"GitHub profile 返回 {resp.status_code}")

        html = resp.text
        frag_m = re.search(
            r'src="(/[^"]*?controller=profiles[^"]*?tab=contributions[^"]*?)"',
            html,
        )
        if not frag_m:
            raise Exception("未找到 contributions fragment URL")

        frag_url = "https://github.com" + frag_m.group(1).replace("&amp;", "&")
        frag_resp = _requests.get(
            frag_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )
        if frag_resp.status_code != 200:
            raise Exception(f"GitHub fragment 返回 {frag_resp.status_code}")

        rects = re.findall(r'data-date="([^"]+)"[^>]*data-level="([^"]+)"', frag_resp.text)
        level_map = {"0": 0, "1": 2, "2": 5, "3": 8, "4": 12}
        contributions = {}
        for date, level in rects:
            count = level_map.get(level, 0)
            if count > 0:
                contributions[date] = count
        return contributions

    # 重试3次
    for attempt in range(3):
        try:
            contributions = _do_fetch()
            print(f"GitHub: fetched {len(contributions)} days of contributions", flush=True)
            _github_cache["data"] = contributions
            _github_cache["timestamp"] = now
            # 写入磁盘缓存
            try:
                GITHUB_DISK_CACHE.write_text(
                    json.dumps({"data": contributions, "ts": now}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return contributions
        except Exception as e:
            print(f"GitHub fetch attempt {attempt+1}/3 failed: {e}", flush=True)
            if attempt < 2:
                import time as _t
                _t.sleep(2)

    print("GitHub: 所有重试均失败", flush=True)
    return _github_cache["data"] or {}


def fetch_all_data() -> dict:
    """获取所有数据（带缓存）"""
    now = time.time()
    if _cache["data"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["data"]

    try:
        api = get_mimo_api()

        # 获取按天明细（本月）- 先获取以避免 session 状态问题
        # 注意：MiMo 平台按 UTC（世界时）分组统计每日用量，
        # 北京时间 0:00-8:00 时 UTC 仍是前一天，需按 UTC 年月请求，
        # 否则月初这段时间会请求到还没有数据的新月份。
        utc_now = datetime.utcnow()
        year = utc_now.year
        month = utc_now.month
        daily_resp = api.session.get(
            f"https://platform.xiaomimimo.com/api/v1/usage/detail?year={year}&month={month}",
            timeout=15,
        )
        daily_detail = daily_resp.json()

        # 并发获取数据
        profile = api.get_user_profile()
        plan = api.get_token_plan_detail()
        usage = api.get_token_plan_usage()
        balance = api.get_balance()
        payg_usage = api.get_usage()
        tp_usage_detail = api.get_token_plan_usage_detail()

        daily_data = daily_detail.get("data", {})

        # 获取所有本地平台今日使用量（聚合）
        local_apis = get_local_apis()
        local_usage = {
            "requestCount": 0,
            "totalInputTokens": 0,
            "totalOutputTokens": 0,
            "totalCacheReadTokens": 0,
            "totalTokens": 0,
            "totalReasoningTokens": 0,
            "totalCost": 0,
            "errorCount": 0,
            "meterUsage": 0,
        }
        has_local = False
        for api in local_apis:
            today = api.get_today_usage()
            if today:
                has_local = True
                local_usage["requestCount"] += today.get("requestCount", 0)
                # 本地平台: totalTokens = in + out + cacheRead
                local_usage["totalInputTokens"] += today.get("totalInputTokens", 0)
                local_usage["totalOutputTokens"] += today.get("totalOutputTokens", 0)
                local_usage["totalCacheReadTokens"] += today.get("totalCacheReadTokens", 0)
                local_usage["totalTokens"] += today.get("totalTokens", 0)
                local_usage["totalReasoningTokens"] += today.get("totalReasoningTokens", 0)
                local_usage["totalCost"] += today.get("totalCost", 0)
                local_usage["errorCount"] += today.get("errorCount", 0)
                local_usage["meterUsage"] += today.get("meterUsage", 0)

        # 计算 MiMo 的 inMiss（非缓存输入）
        mimo_inMiss = 0
        tu = daily_data.get("tokenUsage", [])
        bj_now = datetime.now(timezone(timedelta(hours=8)))
        ref = datetime.utcnow() if bj_now.hour >= 8 else datetime.utcnow() - timedelta(days=1)
        target_key = f"{ref.month:02d}-{ref.day:02d}"
        for t in tu:
            if t[0] == target_key:
                mimo_inMiss = max(0, t[1] - t[4])  # inTok - cache
                break

        result = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "profile": profile.get("data", {}),
            "plan": plan.get("data", {}),
            "usage": usage.get("data", {}),
            "balance": balance.get("data", {}),
            "payg_usage": payg_usage.get("data", {}),
            "tp_usage_detail": tp_usage_detail.get("data", []),
            "daily_detail": daily_data,
            "local_usage": local_usage if has_local else None,
            "mimo_inMiss": mimo_inMiss,
        }

        _cache["data"] = result
        _cache["timestamp"] = now
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


@app.route("/")
def index():
    """返回看板页面"""
    return send_from_directory("static", "dashboard.html")


def _get_gpus() -> list:
    """通过 PowerShell 获取 GPU 信息 + 利用率"""
    import subprocess, re as _re
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    if not Path(ps_path).exists():
        return []
    try:
        # 1. 获取 GPU 基本信息
        r = subprocess.run(
            [ps_path, "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, PNPDeviceID | ConvertTo-Json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        raw = json.loads(r.stdout) if r.stdout.strip() else []
        if isinstance(raw, dict):
            raw = [raw]
        gpus = []
        for g in raw:
            name = g.get("Name", "")
            if "Idd" in name or "Microsoft" in name:
                continue
            vram = g.get("AdapterRAM", 0)
            if isinstance(vram, str):
                vram = int(vram) if vram.isdigit() else 0
            pnp = g.get("PNPDeviceID", "")
            vram = g.get("AdapterRAM", 0)
            if isinstance(vram, str):
                vram = int(vram) if vram.isdigit() else 0
            # WMI AdapterRAM 对很多显卡不准确，用已知型号映射修正
            _VRAM_MAP = {
                "9070 XT": 16 * 1024**3,   # RX 9070 XT = 16GB
                "9070": 16 * 1024**3,
                "7900 XTX": 24 * 1024**3,
                "7900 XT": 20 * 1024**3,
                "7900 GRE": 16 * 1024**3,
                "7800 XT": 16 * 1024**3,
                "7700 XT": 12 * 1024**3,
                "7600": 8 * 1024**3,
                "4090": 24 * 1024**3,
                "4080": 16 * 1024**3,
                "4070 Ti": 12 * 1024**3,
                "4070": 12 * 1024**3,
                "4060 Ti": 16 * 1024**3,
                "4060": 8 * 1024**3,
            }
            for key, val in _VRAM_MAP.items():
                if key.lower() in name.lower():
                    vram = val
                    break
            gpus.append({"name": name, "vram": vram, "util": None, "pnp": pnp})

        # 2. 通过 GPU Adapter Memory 计数器获取 LUID 映射
        #    每个 adapter 有独立的 LUID，可以用来匹配引擎利用率
        r_adpm = subprocess.run(
            [ps_path, "-NoProfile", "-Command",
             r'Get-Counter "\GPU Adapter Memory(*)\Dedicated Usage" -ErrorAction SilentlyContinue '
             r'| ForEach-Object { $_.CounterSamples | Select-Object Path, CookedValue | ConvertTo-Json }'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        luid_vram = {}  # low_luid -> vram_bytes
        if r_adpm.stdout.strip():
            adpm_data = json.loads(r_adpm.stdout)
            if isinstance(adpm_data, dict):
                adpm_data = [adpm_data]
            for d in adpm_data:
                m = _re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", d.get("Path", ""))
                if m:
                    low_luid = m.group(1).lower()
                    luid_vram[low_luid] = luid_vram.get(low_luid, 0) + int(d.get("CookedValue", 0))

        # 3. 匹配 LUID -> GPU adapter
        #    通过 PCI 设备 ID 识别独显（非 APU 集成显卡的 Device ID 范围）
        #    独显优先匹配显存用量最高的 LUID
        _APU_DEVS = {"13c0", "13e0", "15bf", "1681", "164f", "15e4"}  # 常见 AMD APU iGPU 设备 ID
        def _is_discrete(gpu):
            pnp = gpu.get("pnp", "")
            m = _re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
            return m and m.group(1).lower() not in _APU_DEVS

        luid_to_gpu = {}
        sorted_luids = sorted(luid_vram.items(), key=lambda x: -x[1])
        # 优先分配显存最高的 LUID 给独显
        for low_luid, vram_bytes in sorted_luids:
            if vram_bytes <= 0:
                continue
            matched_ids = [id(v) for v in luid_to_gpu.values()]
            # 优先匹配独显
            target = None
            for gpu in gpus:
                if id(gpu) not in matched_ids and _is_discrete(gpu):
                    target = gpu
                    break
            if target is None:
                # fallback: 匹配任意未匹配的 GPU
                for gpu in gpus:
                    if id(gpu) not in matched_ids:
                        target = gpu
                        break
            if target:
                luid_to_gpu[low_luid] = target
                # 用实际显存占用替换 AdapterRAM（AMD 新卡的 AdapterRAM 不准）
                target["vram_used"] = vram_bytes

        # 4. 从 GPU Engine 计数器获取利用率，按 LUID 聚合
        r_engine = subprocess.run(
            [ps_path, "-NoProfile", "-Command",
             "Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
             " | Select-Object Name, UtilizationPercentage | ConvertTo-Json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if r_engine.stdout.strip():
            engines = json.loads(r_engine.stdout)
            if isinstance(engines, dict):
                engines = [engines]
            # 按 LUID 聚合，取每个 LUID 的最大利用率
            luid_util = {}
            for e in engines:
                m = _re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", e.get("Name", ""))
                if m:
                    low_luid = m.group(1).lower()
                    u = e.get("UtilizationPercentage") or 0
                    luid_util[low_luid] = max(luid_util.get(low_luid, 0), u)
            # 写入对应 GPU
            for low_luid, util in luid_util.items():
                if low_luid in luid_to_gpu:
                    luid_to_gpu[low_luid]["util"] = max(luid_to_gpu[low_luid]["util"] or 0, util)

        return gpus
    except Exception:
        return []


def _get_disks() -> list:
    """获取物理磁盘信息（按物理盘分组）"""
    import subprocess
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    try:
        # 获取物理磁盘 + 其分区的使用情况
        r = subprocess.run(
            [ps_path, "-NoProfile", "-Command",
             r"""Get-PhysicalDisk | ForEach-Object {
                 $pd = $_
                 $parts = Get-Partition -DiskNumber $pd.DeviceId -ErrorAction SilentlyContinue |
                     Where-Object { $_.Type -ne 'Reserved' -and $_.Size -gt 0 }
                 $totalPart = ($parts | Measure-Object -Property Size -Sum).Sum
                 $driveLetters = ($parts | Where-Object { $_.DriveLetter } | Select-Object -ExpandProperty DriveLetter) -join ''
                 $usedPart = 0
                 foreach ($dl in $driveLetters.ToCharArray()) {
                     $vol = Get-Volume -DriveLetter $dl -ErrorAction SilentlyContinue
                     if ($vol) { $usedPart += ($vol.Size - $vol.SizeRemaining) }
                 }
                 $mediaType = [string]$pd.MediaType
                 [PSCustomObject]@{
                     Model = $pd.FriendlyName
                     Total = $totalPart
                     Used = $usedPart
                     Letters = $driveLetters
                     MediaType = $mediaType
                 }
             } | ConvertTo-Json -Depth 3"""],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        phys_disks = json.loads(r.stdout) if r.stdout.strip() else []
        if isinstance(phys_disks, dict):
            phys_disks = [phys_disks]
    except Exception:
        phys_disks = []

    disks = []
    for pd in phys_disks:
        total = pd.get("Total", 0) or 0
        used = pd.get("Used", 0) or 0
        percent = round(used / total * 100, 1) if total > 0 else 0
        model = pd.get("Model", "Unknown")
        letters = pd.get("Letters", "")
        if letters:
            model += f" ({letters})"
        disks.append({
            "model": model,
            "total": total,
            "used": used,
            "percent": percent,
            "type": pd.get("MediaType", "Unknown"),
        })
    return disks


def get_system_info() -> dict:
    """获取系统硬件信息（双层缓存：静态5分钟，动态1秒）"""
    now = time.time()

    # ── 动态缓存（1秒）──
    if not hasattr(get_system_info, "_dyn_cache"):
        get_system_info._dyn_cache = {"data": None, "ts": 0}
    dyn = get_system_info._dyn_cache
    if dyn["data"] and (now - dyn["ts"]) < 1:
        return dyn["data"]

    # ── 静态缓存（5分钟）：CPU型号、内存频率、GPU名称/显存、磁盘型号 ──
    if not hasattr(get_system_info, "_static"):
        get_system_info._static = {"data": None, "ts": 0}
    static = get_system_info._static
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
                import re as _re_gpu
                dm = _re_gpu.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
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
    if not hasattr(get_system_info, "_net_prev"):
        get_system_info._net_prev = {"sent": net.bytes_sent, "recv": net.bytes_recv, "ts": now}
    np = get_system_info._net_prev
    dt = now - np["ts"]
    rate_up = max(0, (net.bytes_sent - np["sent"]) / dt) if dt > 0 else 0
    rate_down = max(0, (net.bytes_recv - np["recv"]) / dt) if dt > 0 else 0
    get_system_info._net_prev = {"sent": net.bytes_sent, "recv": net.bytes_recv, "ts": now}

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


def _run_ps(script: str, timeout=8) -> str:
    """执行一次 PowerShell，返回 stdout（使用临时脚本文件避免转义问题）"""
    import subprocess as _sp
    import tempfile, os
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8")
    try:
        tmp.write(script)
        tmp.close()
        r = _sp.run(
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
    import re as _re
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
            m = _re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", e.get("Name", ""))
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
            m = _re.search(r"luid_0x[0-9a-fA-F]+_0x([0-9a-fA-F]+)", d.get("Path", ""))
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
            dm = _re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
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
                dm = _re.search(r"DEV_([0-9A-Fa-f]{4})", pnp)
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
        import re as _re2
        for dk in disks:
            letters_str = dk.get("model", "")
            m = _re2.search(r"\(([A-Z]+)\)", letters_str)
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


@app.route("/api/data")
def api_data():
    """返回所有 MiMo 数据 + GitHub 贡献"""
    data = fetch_all_data()
    data["github"] = {"user": GITHUB_USER, "contributions": fetch_github_contributions()}
    return jsonify(data)


# ============================================================
# SMTC 媒体信息 + 网易云歌词
# ============================================================

import re as _re_media
import subprocess as _media_sp
import threading

# 歌词缓存：以 (title, artist) 作为 key，避免同一首歌反复调用网易云 API。
# 用锁保护，且搜索失败时不写入缓存，允许下次请求重试（而不是永久卡住没有歌词）。
_lyrics_cache = {}   # {(title, artist): {"song_id":.., "duration":.., "lyrics":[...]}}
_lyrics_cache_lock = threading.Lock()
_LYRICS_CACHE_MAX = 20  # 最多缓存 20 首歌，超出后清空最旧的（简单 FIFO，避免内存无限增长）
_lyrics_cache_order = []

# 常驻 SMTC 子进程（避免 pywebview COM 冲突 + 避免重复拉起进程的开销）
# 子进程同时用 UI Automation 读取网易云播放进度条比例（SMTC 本身不提供播放进度）
_smtc_result = {"status": "idle", "title": "", "artist": "", "progress_ratio": None}
_smtc_lock = threading.Lock()
_smtc_last_update = 0.0
_SMTC_WORKER = str(Path(__file__).parent / "smtc_worker.py")
_SMTC_PYTHON = str(Path(__file__).parent / "venv" / "Scripts" / "python.exe")
_smtc_started = False


def _smtc_reader_loop():
    """启动常驻子进程，持续读取每一行 JSON 输出并更新缓存"""
    global _smtc_result, _smtc_last_update
    import time as _time
    while True:
        proc = None
        try:
            proc = _media_sp.Popen(
                [_SMTC_PYTHON, _SMTC_WORKER],
                stdout=_media_sp.PIPE, stderr=_media_sp.DEVNULL,
                bufsize=1,
            )
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    info = json.loads(line)
                    with _smtc_lock:
                        _smtc_result = info
                        _smtc_last_update = _time.time()
                except json.JSONDecodeError:
                    continue
            # 子进程意外退出，等待后重启
            proc.wait(timeout=1)
        except Exception as e:
            print(f"[media] worker error: {e}", flush=True)
        finally:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        _time.sleep(2)  # 重启前等待


def _ensure_smtc_thread():
    """确保 SMTC 常驻子进程的读取线程已启动"""
    global _smtc_started
    if not _smtc_started:
        _smtc_started = True
        t = threading.Thread(target=_smtc_reader_loop, daemon=True)
        t.start()


def _parse_lrc(lrc_text: str) -> list:
    """解析 LRC 歌词为 [(seconds, text), ...]"""
    result = []
    for line in lrc_text.split("\n"):
        matches = _re_media.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        text = _re_media.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line).strip()
        if not text or not matches:
            continue
        for m, s in matches:
            sec = int(m) * 60 + float(s)
            result.append((sec, text))
    result.sort(key=lambda x: x[0])
    return result


def _search_netease(title: str, artist: str) -> tuple:
    """搜索网易云获取歌曲 ID 和总时长（秒）"""
    try:
        resp = _requests.get(
            "https://music.163.com/api/search/get",
            params={"s": f"{title} {artist}", "type": 1, "limit": 5, "offset": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        songs = data.get("result", {}).get("songs", [])
        if songs:
            song = songs[0]
            duration_sec = (song.get("duration") or 0) / 1000.0
            return song["id"], duration_sec
    except Exception as e:
        print(f"[media] search error: {e}", flush=True)
    return 0, 0.0


def _fetch_lyrics(song_id: int) -> str:
    """从网易云获取 LRC 歌词"""
    try:
        resp = _requests.get(
            f"http://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        return data.get("lrc", {}).get("lyric", "")
    except Exception as e:
        print(f"[media] lyrics error: {e}", flush=True)
    return ""


async def _get_smtc_info() -> dict:
    """通过 SMTC 获取当前播放信息"""
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        sessions = await MediaManager.request_async()
        session = sessions.get_current_session()
        if not session:
            return {"status": "idle", "title": "", "artist": "", "position": 0, "duration": 0}

        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        playback = session.get_playback_info()

        position = timeline.position.total_seconds()
        duration = timeline.end_time.total_seconds()

        # playback_status: 0=closed, 1=opened, 2=changing, 3=stopped, 4=playing, 5=paused
        status_map = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
        status = status_map.get(playback.playback_status, "unknown")

        return {
            "status": status,
            "title": props.title or "",
            "artist": props.artist or "",
            "position": round(position, 1),
            "duration": round(duration, 1),
        }
    except Exception as e:
        print(f"[media] SMTC error: {e}", flush=True)
        return {"status": "error", "title": "", "artist": "", "position": 0, "duration": 0}


def _get_lyrics_for(title: str, artist: str) -> dict:
    """获取指定歌曲的歌词信息，带缓存（key=title+artist）。
    搜索/歌词请求失败时不写入缓存，允许下次调用重试，避免永久卡住无歌词。"""
    key = (title, artist)

    with _lyrics_cache_lock:
        cached = _lyrics_cache.get(key)
    if cached is not None:
        return cached

    song_id, duration_sec = _search_netease(title, artist)
    if not song_id:
        # 搜索失败：不缓存，下次请求会重新尝试
        return {"song_id": None, "duration": 0.0, "lyrics": []}

    lrc_text = _fetch_lyrics(song_id)
    lyrics = _parse_lrc(lrc_text) if lrc_text else []
    result = {"song_id": song_id, "duration": duration_sec, "lyrics": lyrics}

    if lyrics:
        # 只有成功拿到歌词才写入缓存
        with _lyrics_cache_lock:
            _lyrics_cache[key] = result
            _lyrics_cache_order.append(key)
            while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
                old_key = _lyrics_cache_order.pop(0)
                _lyrics_cache.pop(old_key, None)
        print(f"[media] loaded lyrics for: {title} ({len(lyrics)} lines, "
              f"duration={duration_sec:.1f}s)", flush=True)

    return result


def get_media_info() -> dict:
    """获取完整媒体信息 + 当前歌词"""
    _ensure_smtc_thread()
    with _smtc_lock:
        info = dict(_smtc_result)

    if info["status"] not in ("playing", "paused") or not info["title"]:
        return {
            "status": info["status"], "title": "", "artist": "", "lyric": "",
            "position": 0, "duration": 0, "progress_ratio": None, "position_source": "none",
        }

    lyric_data = _get_lyrics_for(info["title"], info["artist"])
    lyrics = lyric_data["lyrics"]
    duration = lyric_data["duration"]

    ratio = info.get("progress_ratio")
    position_source = "none"
    pos = 0.0

    if ratio is not None and duration > 0:
        # UI Automation 读到的真实进度条比例（可靠）
        pos = ratio * duration
        position_source = "uia"
    # 若 UIA 不可用（网易云窗口最小化/隐藏），前端会自行按估算计时兜底，
    # 后端不再返回假的 position，避免误导。

    # 根据播放位置匹配当前歌词行
    current_lyric = ""
    next_lyric = ""
    if lyrics and pos > 0:
        for i, (t, text) in enumerate(lyrics):
            if t <= pos:
                current_lyric = text
                if i + 1 < len(lyrics):
                    next_lyric = lyrics[i + 1][1]
            else:
                break

    return {
        "status": info["status"],
        "title": info["title"],
        "artist": info["artist"],
        "lyric": current_lyric,
        "next_lyric": next_lyric,
        "lyrics": [[t, text] for t, text in lyrics],
        "position": round(pos, 2),
        "duration": round(duration, 2),
        "progress_ratio": ratio,
        "position_source": position_source,
    }


@app.route("/api/media")
def api_media():
    """返回当前播放的媒体信息和歌词"""
    return jsonify(get_media_info())


@app.route("/api/media/reload", methods=["POST"])
def api_media_reload():
    """清除当前歌曲的歌词缓存并重新获取"""
    with _smtc_lock:
        title = _smtc_result.get("title", "")
        artist = _smtc_result.get("artist", "")
    if title:
        key = (title, artist)
        with _lyrics_cache_lock:
            _lyrics_cache.pop(key, None)
            try:
                _lyrics_cache_order.remove(key)
            except ValueError:
                pass
        print(f"[media] lyrics cache cleared for: {title}", flush=True)
    return jsonify(get_media_info())


@app.route("/api/system")
def api_system():
    """返回系统硬件信息（独立端点，不依赖 MiMo 登录）"""
    return jsonify(get_system_info())


@app.route("/api/nug")
def api_nug():
    """返回 NUG 平台余额和用量"""
    nug = get_nug_api()
    if not nug:
        return jsonify({"enabled": False})
    data = nug.get_data()
    if data is None:
        return jsonify({"enabled": True, "error": "获取数据失败"})
    return jsonify({"enabled": True, **data})


def main():
    parser = argparse.ArgumentParser(description="MiMo Usage Dashboard")
    parser.add_argument("--port", "-p", type=int, default=5000, help="端口号 (默认 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--open", "-o", action="store_true", help="自动打开浏览器")
    args = parser.parse_args()

    if args.open:
        import webbrowser
        url = f"http://{args.host}:{args.port}"
        print(f"正在打开浏览器: {url}")
        webbrowser.open(url)

    print(f"MiMo Dashboard 启动中...")
    print(f"访问地址: http://{args.host}:{args.port}")
    print(f"按 Ctrl+C 停止服务器")

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
