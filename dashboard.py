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
import os
import platform
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import psutil

try:
    from flask import Flask, abort, jsonify, send_from_directory, request
except ImportError:
    print("错误: 缺少 Flask 库，请运行: pip install flask")
    sys.exit(1)

try:
    from flask_sock import Sock
except ImportError:
    print("错误: 缺少 flask-sock 库，请运行: pip install flask-sock")
    sys.exit(1)

# 从 mimo_usage.py 导入 MiMoAPI
try:
    from mimo_usage import MiMoAPI, load_cookies, refresh_mimo_cookie, save_cookies
except ImportError:
    print("错误: 无法导入 mimo_usage.py，请确保文件存在")
    sys.exit(1)

import requests as _requests

app = Flask(__name__, static_folder="static")
sock = Sock(app)

# ============================================================
# WebSocket 统一数据推送
# ============================================================

import threading as _ws_threading

_ws_clients = []
_ws_client_states = {}
_ws_clients_lock = _ws_threading.Lock()
_ws_vibe_coding = False  # 前端 Vibe Coding 状态（任一客户端开启即生效）
_ws_broadcaster_started = False
_ws_broadcaster_lock = _ws_threading.Lock()


def _ws_recalc_vibe_locked() -> bool:
    """在已持有 _ws_clients_lock 时重新计算全局 Vibe Coding 状态。"""
    global _ws_vibe_coding
    _ws_vibe_coding = any(s.get("vibe") for s in _ws_client_states.values())
    return _ws_vibe_coding


def _ws_has_clients() -> bool:
    """线程安全地判断是否有 WebSocket 客户端。"""
    with _ws_clients_lock:
        return bool(_ws_clients)


def _ws_clients_snapshot() -> list:
    """线程安全地获取 WebSocket 客户端快照。"""
    with _ws_clients_lock:
        return list(_ws_clients)


def _ws_broadcast(msg: dict):
    """向所有连接的客户端广播消息，失败时自动清理。"""
    data = json.dumps(msg, ensure_ascii=False)
    dead = []
    for ws in _ws_clients_snapshot():
        try:
            ws.send(data)
        except Exception:
            dead.append(ws)
    if dead:
        with _ws_clients_lock:
            for ws in dead:
                if ws in _ws_clients:
                    _ws_clients.remove(ws)
                _ws_client_states.pop(ws, None)
            _ws_recalc_vibe_locked()


@sock.route("/ws")
def ws_handler(ws):
    """WebSocket 端点：前端建立连接后接收后端推送 + 前端指令。"""
    with _ws_clients_lock:
        _ws_clients.append(ws)
        _ws_client_states[ws] = {"vibe": False}
        total = len(_ws_clients)
    print(f"[ws] client connected (total: {total})", flush=True)
    try:
        while ws.connected:
            raw = ws.receive(timeout=30)
            if raw:
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "vibe":
                        with _ws_clients_lock:
                            _ws_client_states.setdefault(ws, {})["vibe"] = bool(msg.get("active"))
                            vibe = _ws_recalc_vibe_locked()
                        print(f"[ws] vibe coding: {'ON' if vibe else 'OFF'}", flush=True)
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass
    finally:
        with _ws_clients_lock:
            if ws in _ws_clients:
                _ws_clients.remove(ws)
            _ws_client_states.pop(ws, None)
            vibe = _ws_recalc_vibe_locked()
            total = len(_ws_clients)
        print(f"[ws] client disconnected (total: {total}, vibe: {'ON' if vibe else 'OFF'})", flush=True)


def _ws_broadcaster():
    """后台线程：并行获取 system + media，定时广播。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _executor = ThreadPoolExecutor(max_workers=4)
    _nug_counter = 0
    while True:
        t0 = time.time()
        try:
            if _ws_has_clients():
                # system + media 并行获取
                futs = {
                    _executor.submit(get_system_info): "system",
                    _executor.submit(get_media_info): "media",
                }
                for fut in as_completed(futs):
                    msg_type = futs[fut]
                    try:
                        _ws_broadcast({"type": msg_type, "data": fut.result()})
                    except Exception as e:
                        print(f"[ws] {msg_type} broadcast error: {e}", flush=True)

                # mimo + nug：Coding 模式 20 秒，Chilling 模式 60 秒
                _nug_counter += 1
                mimo_interval = 20 if _ws_vibe_coding else 60
                if _nug_counter >= mimo_interval:
                    _nug_counter = 0
                    try:
                        _ws_broadcast({"type": "mimo", "data": fetch_all_data()})
                    except Exception as e:
                        print(f"[ws] mimo broadcast error: {e}", flush=True)
                    try:
                        nug = get_nug_api()
                        nug_data = nug.get_data() if nug else None
                        _ws_broadcast({"type": "nug", "data": {"enabled": bool(nug), **(nug_data or {})}})
                    except Exception as e:
                        print(f"[ws] nug broadcast error: {e}", flush=True)
        except Exception as e:
            print(f"[ws] broadcaster error: {e}", flush=True)
        # 精确计时：扣除执行耗时，保证 1 秒间隔
        elapsed = time.time() - t0
        time.sleep(max(0, 1.0 - elapsed))


def start_background_threads_once() -> bool:
    """启动后台线程；多次调用只会真正启动一次。"""
    global _ws_broadcaster_started
    with _ws_broadcaster_lock:
        if _ws_broadcaster_started:
            return False
        t = _ws_threading.Thread(target=_ws_broadcaster, daemon=True, name="ws-broadcaster")
        t.start()
        _ws_broadcaster_started = True
        return True

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
_DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN") or secrets.token_urlsafe(24)


def load_config() -> dict:
    """加载本地私有配置文件。"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_dashboard_token() -> str:
    """获取 POST 防护 token；优先读取配置/环境变量，否则使用启动时随机值。"""
    config = load_config()
    token = (config.get("dashboard") or {}).get("token") or os.environ.get("DASHBOARD_TOKEN")
    return str(token or _DASHBOARD_TOKEN)


def _same_site_from_header(value: str | None) -> bool:
    """校验 Origin/Referer 是否指向当前 dashboard 同源。"""
    if not value:
        return False
    try:
        parsed = urlparse(value)
        host_url = urlparse(request.host_url)
        return parsed.scheme == host_url.scheme and parsed.netloc == host_url.netloc
    except Exception:
        return False


def require_post_protection():
    """对本地状态修改类 POST 做最小 CSRF/token 防护。"""
    if request.method != "POST":
        return
    expected = get_dashboard_token()
    provided = request.headers.get("X-Dashboard-Token")
    if provided and secrets.compare_digest(provided, expected):
        return
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if _same_site_from_header(origin) or _same_site_from_header(referer):
        return
    abort(403)


# ============================================================
# 显示主题管理
# ============================================================

THEME_FILE = Path(__file__).parent / "display_theme.json"

# 每个主题包含 name + 背景配置（bg_type: "image" | "color"）
_THEMES = [
    {
        "name": "dark",
        "bg_type": "image",
        "bg_image": "/static/bg/101b3e01db1548b96ea5413ce9bbe1d8.jpg",
        "bg_color": "#0a0618",
    },
    {
        "name": "mono",
        "bg_type": "color",
        "bg_color": "#f8f8fa",
    },
]


def _theme_response(idx: int) -> dict:
    """构建标准的主题 API 响应"""
    t = _THEMES[idx]
    return {
        "theme": t["name"],
        "index": idx,
        "themes": [t["name"] for t in _THEMES],
        "bg": {k: t[k] for k in ("bg_type", "bg_image", "bg_color") if k in t},
    }


def _theme_index_by_name(name: str | None) -> int | None:
    """根据主题名查找索引。"""
    for i, theme in enumerate(_THEMES):
        if theme["name"] == name:
            return i
    return None


def _load_theme() -> int:
    """读取当前主题索引；兼容旧的 {"index": 0} 格式。"""
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
        if "theme" in data:
            idx = _theme_index_by_name(data.get("theme"))
            if idx is not None:
                return idx
        idx = int(data.get("index", 0))
        if 0 <= idx < len(_THEMES):
            return idx
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        pass
    return 0


def _save_theme(index: int):
    """持久化主题名到磁盘。"""
    try:
        THEME_FILE.write_text(json.dumps({"theme": _THEMES[index]["name"]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _set_theme_response(idx: int) -> dict:
    """保存主题并广播给所有客户端。"""
    _save_theme(idx)
    data = _theme_response(idx)
    _ws_broadcast({"type": "theme", "data": data})
    return data


@app.route("/api/theme", methods=["GET", "POST"])
def api_theme_get_or_set():
    """GET 返回当前主题；POST 指定主题。"""
    if request.method == "GET":
        return jsonify(_theme_response(_load_theme()))
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    idx = _theme_index_by_name(payload.get("theme"))
    if idx is None:
        return jsonify({"error": "unknown theme", "themes": [t["name"] for t in _THEMES]}), 400
    return jsonify(_set_theme_response(idx))


@app.route("/api/theme/next", methods=["POST"])
def api_theme_next():
    """循环切换到下一个主题。"""
    require_post_protection()
    idx = (_load_theme() + 1) % len(_THEMES)
    return jsonify(_set_theme_response(idx))


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


_mimo_cookie_valid = None  # None=未检测, True=有效, False=过期
_mimo_cookie_last_check = 0  # 上次检测时间戳


def get_mimo_api() -> MiMoAPI | None:
    """获取 MiMoAPI 实例，自动检测 Cookie 有效性。
    过期时尝试用 passToken 自动刷新，刷新失败才返回 None。
    """
    global _mimo_cookie_valid, _mimo_cookie_last_check
    cache_info = load_cookies()
    cookie_str = cache_info.get("cookie")
    if not cookie_str:
        print("[MiMo] 未找到 Cookie，请先运行 mimo_usage.py --login qr --save 登录", flush=True)
        _mimo_cookie_valid = False
        return None

    api = MiMoAPI(cookie_str)

    # 每 5 分钟最多检测一次
    now = time.time()
    if _mimo_cookie_valid is not None and (now - _mimo_cookie_last_check) < 300:
        return api if _mimo_cookie_valid else None

    _mimo_cookie_last_check = now

    try:
        test = api.get_user_profile()
        if test.get("code") == 401:
            # Cookie 过期，尝试用 passToken 自动刷新
            print("[MiMo] Cookie 已过期，尝试自动刷新...", flush=True)
            new_cookie = refresh_mimo_cookie(cookie_str)
            if new_cookie:
                # 刷新成功，保存新 cookie
                save_cookies(new_cookie, cache_info.get("method", "qr"), {
                    k: v for k, v in cache_info.items()
                    if k not in ("cookie", "method", "saved_at")
                })
                api = MiMoAPI(new_cookie)
                _mimo_cookie_valid = True
                print("[MiMo] 自动刷新成功，已保存新 Cookie [OK]", flush=True)
            else:
                print("[MiMo] 自动刷新失败，请手动运行: python mimo_usage.py --login qr --save", flush=True)
                _mimo_cookie_valid = False
        else:
            _mimo_cookie_valid = True
            print("[MiMo] Cookie 有效 [OK]", flush=True)
    except Exception as e:
        print(f"[MiMo] Cookie 检测失败: {e}", flush=True)
        _mimo_cookie_valid = False

    return api if _mimo_cookie_valid else None


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
        if api is None:
            # Cookie 过期，返回最小数据 + 过期标记
            return {
                "success": False,
                "mimo_expired": True,
                "profile": {},
                "plan": {},
                "usage": {},
                "balance": {},
                "payg_usage": {},
                "daily_detail": {},
                "tp_usage_detail": [],
                "local_usage": {},
                "mimo_inMiss": 0,
                "github": {},
                "system": {},
                "timestamp": time.time(),
            }

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


@app.after_request
def no_cache_static(response):
    """禁止浏览器缓存静态文件，改了 HTML/CSS/JS 刷新即生效，不用重启"""
    if request.path.startswith("/static/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/")
def index():
    """返回看板页面"""
    return send_from_directory("static", "dashboard.html")


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


@app.route("/api/data")
def api_data():
    """返回所有 MiMo 数据 + GitHub 贡献"""
    data = fetch_all_data()
    data["github"] = {"user": GITHUB_USER, "contributions": fetch_github_contributions()}
    return jsonify(data)


# ============================================================
# SMTC 媒体信息 + 网易云歌词
# ============================================================

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
        matches = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        text = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line).strip()
        if not text or not matches:
            continue
        for m, s in matches:
            sec = int(m) * 60 + float(s)
            result.append((sec, text))
    result.sort(key=lambda x: x[0])
    return result


# 翻唱/切片/改编关键词。仅当"候选歌名有这些词而目标 title 没有"时才惩罚
# （用户听翻唱时目标 title 本身会包含这些词，不应扣分）。
_LYRIC_JUNK_KW = (
    "DJ", "remix", "Remix", "REMIX", "翻自", "原唱", "钢琴", "伴奏",
    "伤感版", "女声", "男声", "Cover", "cover", "Acoustic",
    "Live版", "live版", "Live)", "Live）", "正式版",
    " beat", " Beat", " BEAT", "Type Beat",
)

# 采纳门槛。参考分布：
#   精确艺人 + 完整名字 = 3×10 + 2×3 = 36
#   子串艺人 + 完整名字 = 2×10 + 2×3 = 26
#   伪造艺人（"周杰伦-"匹配"周杰伦"是子串）+ 完整名字 - junk 惩罚 = 26 - 6 = 20
_LYRIC_SCORE_THRESHOLD = 25


def _strip_paren(s: str) -> str:
    """去掉歌名尾部的括号后缀（'起风了 (Acoustic)' -> '起风了'）"""
    s = (s or "").strip().lower()
    for ch in ("(", "（"):
        i = s.find(ch)
        if i > 0:
            s = s[:i].strip()
    return s


# 伪造艺人常见后缀。网易云上大量 AI 翻唱账号叫 "周杰伦-"、"周杰伦."、"周杰伦、"、
# "b-kll" 这种，故意在原艺人名后加少量特殊字符来蹭搜索。
_FAKE_ARTIST_SUFFIX_CHARS = "-.·、_~—,、."


def _is_fake_artist_variant(candidate: str, target: str) -> bool:
    """候选艺人名把尾部特殊字符剥掉后与目标艺人一致 —— 判定为伪造账号。

    真合唱/双名艺人（例："冯沁苑(买辣椒也用券)"）加的是有意义的字符或括号，剥不掉。
    """
    if not candidate or not target:
        return False
    stripped = candidate.rstrip(_FAKE_ARTIST_SUFFIX_CHARS).strip()
    return stripped == target and stripped != candidate


def _has_extra_junk(candidate: str, target: str) -> bool:
    """候选歌名里出现了翻唱/改编关键词，但目标歌名里没有该关键词。

    用户如果本来就在听 'xxx (Acoustic)' 或 'DJ 版 xxx'，目标 title 里就带这些词，
    此时候选带同样的词是想要的匹配，不应视为"多出来的翻唱字样"。
    """
    for kw in _LYRIC_JUNK_KW:
        if kw in candidate and kw not in target:
            return True
    return False


# 内网 ncm-api（NeteaseCloudMusicApiEnhanced）配置。首次调用时从 config.json 读，
# 之后缓存在这里。base_url 为空表示"不启用，走公开老接口 fallback"。
_NCM_API_CONF = None   # None=未初始化，dict={"base_url":"","cookie":""}=已初始化


def _get_ncm_api_conf() -> dict:
    """返回 {'base_url': str, 'cookie': str}。仅在首次调用时读 config.json。"""
    global _NCM_API_CONF
    if _NCM_API_CONF is None:
        try:
            cfg = load_config().get("netease_api") or {}
            if cfg.get("enabled") and cfg.get("base_url"):
                _NCM_API_CONF = {
                    "base_url": cfg["base_url"].rstrip("/"),
                    "cookie": cfg.get("cookie", ""),
                }
            else:
                _NCM_API_CONF = {"base_url": "", "cookie": ""}
        except Exception:
            _NCM_API_CONF = {"base_url": "", "cookie": ""}
    return _NCM_API_CONF


def _get_ncm_api_base() -> str:
    return _get_ncm_api_conf()["base_url"]


def _search_songs(title: str, artist: str) -> list:
    """返回统一格式的候选列表 [{id, name, artists:[str,...], duration_sec}]。

    优先走内网 ncm-api /cloudsearch（返回结构更干净、字段现代），失败时回退直连
    music.163.com/api/search/get。两个接口的返回字段有差异，这里统一成一种。
    """
    kw = f"{title} {artist}"

    base = _get_ncm_api_base()
    if base:
        try:
            resp = _requests.get(
                f"{base}/cloudsearch",
                params={"keywords": kw, "limit": 30},
                timeout=5,
            )
            data = resp.json()
            songs = (data.get("result") or {}).get("songs", []) or []
            return [
                {
                    "id": s.get("id"),
                    "name": s.get("name", "") or "",
                    "artists": [(a.get("name", "") or "") for a in (s.get("ar") or [])],
                    "duration_sec": (s.get("dt") or 0) / 1000.0,
                }
                for s in songs
                if s.get("id")
            ]
        except Exception as e:
            print(f"[media] cloudsearch error (fallback to legacy): {e}", flush=True)

    try:
        resp = _requests.get(
            "https://music.163.com/api/search/get",
            params={"s": kw, "type": 1, "limit": 30, "offset": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        songs = (data.get("result") or {}).get("songs", []) or []
        return [
            {
                "id": s.get("id"),
                "name": s.get("name", "") or "",
                "artists": [(a.get("name", "") or "") for a in (s.get("artists") or [])],
                "duration_sec": (s.get("duration") or 0) / 1000.0,
            }
            for s in songs
            if s.get("id")
        ]
    except Exception as e:
        print(f"[media] legacy search error: {e}", flush=True)
        return []


# 最近播放列表缓存：ncm-api /record/recent/song 反映的是网易云账号云端的播放历史，
# SMTC 报的当前曲一般就是第一条（或前几条中的一条）。这个查询比 cloudsearch 快、准。
# 后端每首歌只查一次匹配就够，不需要每秒刷新；用简单的 TTL 缓存即可。
_recent_cache = {"list": None, "ts": 0.0}
_recent_cache_lock = threading.Lock()
_RECENT_CACHE_TTL = 15.0  # 秒


def _fetch_recent_played(force: bool = False) -> list:
    """拉取网易云账号云端的最近播放列表，返回 [{id, name, artists:[str], duration_sec, play_time_ms}]。

    需要 ncm-api base_url + cookie；任何一个缺失就返回空列表。
    带 15 秒 TTL 缓存，避免每秒刷新时打爆接口。
    """
    import time as _time
    conf = _get_ncm_api_conf()
    base = conf["base_url"]
    cookie = conf["cookie"]
    if not base or not cookie:
        return []

    with _recent_cache_lock:
        cached = _recent_cache["list"]
        age = _time.time() - _recent_cache["ts"]
    if not force and cached is not None and age < _RECENT_CACHE_TTL:
        return cached

    try:
        resp = _requests.get(
            f"{base}/record/recent/song",
            params={"cookie": cookie, "limit": 20},
            timeout=5,
        )
        data = resp.json()
        items = (data.get("data") or {}).get("list") or []
        result = []
        for x in items:
            d = x.get("data") or {}
            sid = d.get("id")
            if not sid:
                continue
            result.append({
                "id": sid,
                "name": d.get("name", "") or "",
                "artists": [(a.get("name") or "") for a in (d.get("ar") or [])],
                "duration_sec": (d.get("dt") or 0) / 1000.0,
                "play_time_ms": x.get("playTime") or 0,
            })
    except Exception as e:
        print(f"[media] recent played error: {e}", flush=True)
        return []

    with _recent_cache_lock:
        _recent_cache["list"] = result
        _recent_cache["ts"] = _time.time()
    return result


def _match_from_recent(title: str, artist: str) -> tuple:
    """在最近播放列表里找与 (title, artist) 匹配的歌，命中返回 (song_id, duration_sec)。

    只看前 5 条，且要求歌名去括号后一致。艺人字段用宽松包含匹配（因为最近播放里
    艺人字段跟 SMTC 报的通常已经一致，不需要复杂打分）。
    """
    items = _fetch_recent_played()
    if not items:
        return 0, 0.0

    target_title = _strip_paren(title)
    target_artist = (artist or "").strip().lower()

    for it in items[:5]:
        if _strip_paren(it["name"]) != target_title:
            continue
        arts = [a.strip().lower() for a in it["artists"]]
        if not target_artist:
            return it["id"], it["duration_sec"]
        # 精确 / 子串 / 反向子串都算命中
        for a in arts:
            if a == target_artist or (a and target_artist and (target_artist in a or a in target_artist)):
                return it["id"], it["duration_sec"]
    return 0, 0.0


def _search_netease(title: str, artist: str) -> list:
    """搜索网易云获取候选歌曲列表，返回 [(score, song_id, duration_sec), ...] 按分数降序。

    匹配优先级：
    1. 已登录时先查网易云账号的"最近播放列表"（/record/recent/song），
       SMTC 报的当前曲通常就是列表最前面几条之一，这条通道最快也最准。
    2. 上一步没命中或没配 cookie 时，走搜索接口 + 打分/门槛过滤伪造艺人。
    """
    # 优先从最近播放列表拿
    sid, dur = _match_from_recent(title, artist)
    if sid:
        return [(999, sid, dur)]  # 最高优先级

    songs = _search_songs(title, artist)

    target_title = _strip_paren(title)
    target_artist = (artist or "").strip().lower()

    candidates = []  # [(score, song_id, duration_sec), ...]
    for s in songs:
        name_full = s.get("name", "") or ""
        if _strip_paren(name_full) != target_title:
            continue

        artists = [(a or "").strip().lower() for a in s.get("artists", [])]

        artist_score = 0
        for a in artists:
            if a == target_artist:
                artist_score = 3
                break
            if _is_fake_artist_variant(a, target_artist):
                continue
            if target_artist and (target_artist in a or a in target_artist):
                artist_score = max(artist_score, 2)

        name_score = 2 if name_full.strip().lower() == (title or "").strip().lower() else 1
        junk_pen = -6 if _has_extra_junk(name_full, title or "") else 0
        total = artist_score * 10 + name_score * 3 + junk_pen

        if total >= _LYRIC_SCORE_THRESHOLD:
            candidates.append((total, s["id"], s.get("duration_sec", 0.0)))

    candidates.sort(key=lambda x: -x[0])

    if not candidates:
        print(f"[media] no acceptable match on netease: {title} - {artist} "
              f"(scanned {len(songs)} candidates)", flush=True)
    return candidates


def _fetch_lyrics_raw(song_id: int) -> dict:
    """从网易云获取原始歌词数据，同时拿 lrc（逐行）和 yrc（逐字）。

    优先走内网 ncm-api（有登录态时能拿到更完整的歌词）；失败回退直连老接口。
    返回 {'lrc': str, 'yrc': str}，字段为空字符串表示该类型不存在。
    """
    conf = _get_ncm_api_conf()
    base = conf["base_url"]

    if base:
        try:
            params = {"id": song_id}
            if conf["cookie"]:
                params["cookie"] = conf["cookie"]
            resp = _requests.get(f"{base}/lyric/new", params=params, timeout=5)
            data = resp.json()
            return {
                "lrc": (data.get("lrc") or {}).get("lyric", "") or "",
                "yrc": (data.get("yrc") or {}).get("lyric", "") or "",
            }
        except Exception as e:
            print(f"[media] ncm-api lyric error (fallback): {e}", flush=True)

    try:
        resp = _requests.get(
            "http://music.163.com/api/song/lyric",
            params={"id": song_id, "lv": -1, "kv": -1, "tv": -1, "yv": -1, "rv": -1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        return {
            "lrc": (data.get("lrc") or {}).get("lyric", "") or "",
            "yrc": (data.get("yrc") or {}).get("lyric", "") or "",
        }
    except Exception as e:
        print(f"[media] legacy lyric error: {e}", flush=True)
    return {"lrc": "", "yrc": ""}


def _parse_yrc(yrc_text: str) -> list:
    """解析逐字歌词。返回 [{"start": ms, "chars": [{"start": ms, "dur": ms, "text": "字"}, ...]}]

    yrc 每行形如 `[16210,3460](16210,670,0)还(16880,410,0)没...`
    - 行头方括号 [start,duration] 是行显示时间戳（毫秒）
    - 每个字符前的圆括号 (charStart,charDur,unknown) 是字符高亮时间戳（毫秒）

    可能开头有 `{"t":0,"c":[...]}` 之类的 JSON 元数据行，跳过。
    """
    result = []
    for line in (yrc_text or "").split("\n"):
        line = line.strip()
        if not line or line.startswith("{"):
            continue

        # 行头 [start,duration]
        header_m = re.match(r"\[(\d+),(\d+)\]", line)
        if not header_m:
            continue
        line_start = int(header_m.group(1))
        rest = line[header_m.end():]

        # 每个字符：(start,dur,x) text
        chars = []
        for m in re.finditer(r"\((\d+),(\d+),\d+\)([^\(]*)", rest):
            text = m.group(3)
            if not text:
                continue
            chars.append({
                "start": int(m.group(1)),
                "dur": int(m.group(2)),
                "text": text,
            })

        if chars:
            result.append({"start": line_start, "chars": chars})

    result.sort(key=lambda r: r["start"])
    return result


def _load_lyrics_by_id(song_id: int, duration_sec: float) -> dict:
    """按 song_id 拉取并解析歌词，返回统一的结果 dict。"""
    raw = _fetch_lyrics_raw(song_id)
    lyrics_lrc = _parse_lrc(raw["lrc"]) if raw["lrc"] else []
    lyrics_yrc = _parse_yrc(raw["yrc"]) if raw["yrc"] else []
    return {
        "song_id": song_id,
        "duration": duration_sec,
        "lyrics": lyrics_lrc,
        "lyrics_yrc": lyrics_yrc,
        "manual": False,
    }


def _get_lyrics_for(title: str, artist: str, song_id: int = 0) -> dict:
    """获取指定歌曲的歌词信息，带缓存（key=title+artist）。
    如果提供了 song_id（来自 YesPlayMusic API），直接用它拉歌词，跳过搜索。
    否则遍历搜索候选，优先返回有逐字歌词(YRC)的版本。
    搜索/歌词请求失败时不写入缓存，允许下次调用重试。
    如果缓存条目标记为 manual=True（用户手动指定 songId），跳过自动搜索直接返回。"""
    key = (title, artist)

    with _lyrics_cache_lock:
        cached = _lyrics_cache.get(key)
    if cached is not None:
        return cached

    result = None

    if song_id:
        # YesPlayMusic API 直接提供了 song_id，跳过搜索
        r = _load_lyrics_by_id(song_id, 0.0)
        if r["lyrics"] or r["lyrics_yrc"]:
            result = r
    else:
        # 没有 song_id，走搜索 + YRC 优先逻辑
        candidates = _search_netease(title, artist)
        if not candidates:
            return {"song_id": None, "duration": 0.0, "lyrics": [], "lyrics_yrc": [], "manual": False}

        best_any = None
        for score, cid, duration_sec in candidates[:5]:
            r = _load_lyrics_by_id(cid, duration_sec)
            if r["lyrics_yrc"]:
                result = r
                break
            if r["lyrics"] and best_any is None:
                best_any = r
        result = result or best_any
    if not result:
        return {"song_id": None, "duration": 0.0, "lyrics": [], "lyrics_yrc": [], "manual": False}

    if result["lyrics"] or result["lyrics_yrc"]:
        with _lyrics_cache_lock:
            _lyrics_cache[key] = result
            _lyrics_cache_order.append(key)
            while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
                old_key = _lyrics_cache_order.pop(0)
                _lyrics_cache.pop(old_key, None)
        print(f"[media] loaded lyrics for: {title} "
              f"(lrc={len(result['lyrics'])} lines, yrc={len(result['lyrics_yrc'])} lines, "
              f"duration={result['duration']:.1f}s)", flush=True)

    return result


def get_media_info() -> dict:
    """获取完整媒体信息 + 当前歌词"""
    _ensure_smtc_thread()
    with _smtc_lock:
        info = dict(_smtc_result)

    if info["status"] not in ("playing", "paused") or not info["title"]:
        return {
            "status": info["status"], "title": "", "artist": "", "lyric": "", "next_lyric": "",
            "lyrics": [], "lyrics_yrc": [], "song_id": None,
            "position": 0, "duration": 0, "progress_ratio": None, "position_source": "none",
        }

    # YesPlayMusic API 直接提供 song_id，跳过搜索
    lyric_data = _get_lyrics_for(info["title"], info["artist"],
                                  song_id=info.get("song_id"))
    lyrics = lyric_data["lyrics"]
    lyrics_yrc = lyric_data.get("lyrics_yrc") or []
    duration = lyric_data["duration"]
    song_id = lyric_data.get("song_id")

    ratio = info.get("progress_ratio")
    position_source = "none"
    pos = 0.0

    # 优先用 YesPlayMusic API 直接返回的 position（最可靠）
    if info.get("position") and info.get("duration"):
        pos = float(info["position"])
        duration = float(info["duration"])  # API 返回的 duration 比歌词 API 更准
        position_source = "api"
    elif ratio is not None and duration > 0:
        # fallback: UIA 进度条比例 × 歌词 API 时长
        pos = ratio * duration
        position_source = "uia"
    # 若都不可用，前端会自行按估算计时兜底

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
        "lyrics_yrc": lyrics_yrc,
        "song_id": song_id,
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
    """清除当前歌曲的歌词缓存并重新获取。同时刷新最近播放列表缓存，
    避免刚切歌就点重载还是命中旧列表。"""
    require_post_protection()
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
        # 让下次 _match_from_recent 强制重取最新列表
        with _recent_cache_lock:
            _recent_cache["ts"] = 0.0
        print(f"[media] lyrics cache cleared for: {title}", flush=True)
    return jsonify(get_media_info())


@app.route("/api/media/set_song_id", methods=["POST"])
def api_media_set_song_id():
    """手动指定当前歌曲的网易云 song_id 并加载对应歌词。

    应对场景：SMTC 报的曲名/艺人在网易云搜索接口里搜不出正版（比如曲库里
    确实存在但搜索索引缺失），用户通过网易云 App 或网页找到真正的 id 后
    通过前端 UI 传进来，直接拉歌词并写入缓存。缓存条目标记 manual=True，
    自动搜索路径不会覆盖它。
    """
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    try:
        song_id = int(payload.get("song_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid song_id"}), 400

    with _smtc_lock:
        title = _smtc_result.get("title", "")
        artist = _smtc_result.get("artist", "")
    if not title:
        return jsonify({"error": "no active song"}), 400

    # 拉歌词，同时尝试从当前搜索给出的 duration_sec 里推断（若拿不到就用 0）
    # 这里 duration 不重要——UIA 进度条比例配合 SMTC 时长即可，前端会自兜。
    result = _load_lyrics_by_id(song_id, 0.0)
    result["manual"] = True

    key = (title, artist)
    with _lyrics_cache_lock:
        _lyrics_cache[key] = result
        if key not in _lyrics_cache_order:
            _lyrics_cache_order.append(key)
        while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
            old_key = _lyrics_cache_order.pop(0)
            _lyrics_cache.pop(old_key, None)
    print(f"[media] manually set song_id={song_id} for: {title}", flush=True)
    return jsonify(get_media_info())


_LYRIC_OFFSET_FILE = Path(__file__).parent / "lyric_offset.json"
_LYRIC_OFFSET_DEFAULT = 1.5


def _load_lyric_offset() -> float:
    """从磁盘读取歌词偏移量（秒）"""
    try:
        data = json.loads(_LYRIC_OFFSET_FILE.read_text(encoding="utf-8"))
        return float(data.get("offset", _LYRIC_OFFSET_DEFAULT))
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return _LYRIC_OFFSET_DEFAULT


def _save_lyric_offset(val: float):
    """持久化歌词偏移量"""
    try:
        _LYRIC_OFFSET_FILE.write_text(json.dumps({"offset": val}), encoding="utf-8")
    except OSError:
        pass


@app.route("/api/media/offset", methods=["GET", "POST"])
def api_media_offset():
    """GET: 返回当前偏移量; POST: 设置偏移量（支持 delta 增量或绝对值）"""
    if request.method == "GET":
        return jsonify({"offset": _load_lyric_offset()})
    require_post_protection()
    payload = request.get_json(silent=True) or {}
    if "delta" in payload:
        val = round((_load_lyric_offset() + float(payload["delta"])) * 10) / 10
    else:
        val = round(float(payload.get("offset", _LYRIC_OFFSET_DEFAULT)) * 10) / 10
    _save_lyric_offset(val)
    return jsonify({"offset": val})


# ============================================================
# SMTC 系统级播放控制
# ============================================================


def _smtc_control(action: str) -> dict:
    """通过 Windows SMTC 发送系统级媒体控制指令"""
    import asyncio

    async def _do():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )
        manager = await MediaManager.request_async()
        session = manager.get_current_session()
        if not session:
            return {"ok": False, "error": "no active session"}

        props = await session.try_get_media_properties_async()
        title = props.title or ""

        if action == "next":
            ok = await session.try_skip_next_async()
        elif action == "prev":
            ok = await session.try_skip_previous_async()
        elif action == "play":
            ok = await session.try_play_async()
        elif action == "pause":
            ok = await session.try_pause_async()
        elif action == "toggle":
            ok = await session.try_toggle_play_pause_async()
        else:
            return {"ok": False, "error": "unknown action"}

        return {"ok": ok, "title": title}

    try:
        return asyncio.run(_do())
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/player/<action>", methods=["POST"])
def api_player_control(action):
    """播放控制：play/pause/next/prev/toggle（通过 Windows SMTC 系统级指令）"""
    require_post_protection()
    allowed = {"play", "pause", "next", "prev", "toggle"}
    if action not in allowed:
        return jsonify({"error": "unknown action"}), 400
    result = _smtc_control(action)
    return jsonify(result), 200 if result.get("ok") else 500


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
    parser.add_argument("--dev", action="store_true", help="开发模式：启用 Flask debug/reloader")
    args = parser.parse_args()

    if args.open:
        import webbrowser
        url = f"http://{args.host}:{args.port}"
        print(f"正在打开浏览器: {url}")
        webbrowser.open(url)

    print("MiMo Dashboard 启动中...")
    print(f"访问地址: http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("[security] 当前不是仅本机监听；POST 接口会要求同源或 X-Dashboard-Token")
    print("按 Ctrl+C 停止服务器")

    # Flask reloader 会先启动父进程；只在实际服务进程中启动后台线程。
    if not args.dev or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_threads_once()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.dev,
        use_reloader=args.dev,
        extra_files=["static/dashboard.html"] if args.dev else None,
    )


if __name__ == "__main__":
    main()
