#!/usr/bin/env python3
"""
MiMo Token Usage Checker
自动登录小米账号，查询 MiMo Token Plan 使用情况。

登录方式:
  1. 扫码登录（推荐） - 用小米手机扫码
  2. 浏览器 Cookie   - 自动从 Chrome/Edge 读取
  3. 密码登录         - 邮箱/手机号 + 密码
  4. 手动 Cookie      - 从浏览器开发者工具复制

使用方式:
    python mimo_usage.py                  # 交互式选择登录方式
    python mimo_usage.py --login qr       # 扫码登录
    python mimo_usage.py --login browser  # 浏览器 Cookie
    python mimo_usage.py --login password # 密码登录
    python mimo_usage.py --cookie FILE    # 从文件加载 Cookie
    python mimo_usage.py --json           # JSON 格式输出
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path
import urllib.parse

try:
    import requests
except ImportError:
    print("错误: 缺少 requests 库，请运行: pip install requests")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ============================================================
# 常量
# ============================================================

MIMO_PLATFORM_BASE = "https://platform.xiaomimimo.com/api/v1"
XIAOMI_ACCOUNT_BASE = "https://account.xiaomi.com/pass"
MIMO_LOGIN_CALLBACK = (
    "https://platform.xiaomimimo.com/sts"
    "?sign=M7gfywevl3CG5YTTcZDifhK6IK8%3D"
    "&followup=https%3A%2F%2Fplatform.xiaomimimo.com%2Fconsole%2Fbalance"
)
MIMO_LOGIN_SID = "api-platform"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
COOKIES_FILE = Path(__file__).resolve().parent.parent / "config" / "cookies.json"
REQUEST_TIMEOUT = 15

# ============================================================
# Cookie 工具函数
# ============================================================


def parse_set_cookies(resp_headers) -> dict:
    """从响应头中解析 Set-Cookie"""
    cookies = {}
    raw_cookies = []
    # requests 库使用 getlist
    if hasattr(resp_headers, "getlist"):
        raw_cookies = resp_headers.getlist("set-cookie")
    elif hasattr(resp_headers, "get_list"):
        raw_cookies = resp_headers.get_list("set-cookie")
    if not raw_cookies:
        raw = resp_headers.get("set-cookie", "")
        if raw:
            raw_cookies = [raw]

    for sc in raw_cookies:
        match = re.match(r"^([^=]+)=([^;]*)", sc)
        if match and match[2] not in ("EXPIRED", ""):
            cookies[match[1].strip()] = match[2].strip()
    return cookies


def cookie_dict_to_str(cookies: dict) -> str:
    """将 Cookie 字典转换为字符串"""
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v and v != "EXPIRED")


def build_platform_cookies(redirect_cookies: dict, auth_cookies: dict) -> str:
    """从所有收集的 Cookie 中构建平台所需的 Cookie 字符串"""
    all_cookies = {**auth_cookies, **redirect_cookies}

    relevant_names = [
        "userId", "serviceToken", "api-platform_serviceToken",
        "api-platform_slh", "api-platform_ph", "cookie-preferences",
        "passToken", "identity_session", "deviceId",
    ]

    parts = []
    for name in relevant_names:
        val = all_cookies.get(name)
        if val and val != "EXPIRED":
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            needs_quotes = any(c in val for c in ["+", "/", "="])
            parts.append(f'{name}="{val}"' if needs_quotes else f"{name}={val}")

    for name, val in all_cookies.items():
        if name.startswith("api-platform") and name not in relevant_names and val and val != "EXPIRED":
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            needs_quotes = any(c in val for c in ["+", "/", "="])
            parts.append(f'{name}="{val}"' if needs_quotes else f"{name}={val}")

    return "; ".join(parts)


# ============================================================
# 登录方式 1: 扫码登录
# ============================================================


class QRCodeLogin:
    """小米扫码登录"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._qr_image_url = None
        self._login_url = None
        self._long_polling_url = None
        self._timeout = None
        self._last_login_data = None  # 保存最近一次扫码返回的原始数据

    def get_qr_code(self) -> bool:
        """步骤1: 获取 QR 码（带重试）"""
        params = {
            "_qrsize": "480",
            "qs": "%3Fsid%3D" + MIMO_LOGIN_SID + "%26_json%3Dtrue",
            "callback": MIMO_LOGIN_CALLBACK,
            "_hasLogo": "false",
            "sid": MIMO_LOGIN_SID,
            "serviceParam": "",
            "_locale": "zh_CN",
            "_dc": str(int(time.time() * 1000)),
        }

        for attempt in range(5):
            try:
                params["_dc"] = str(int(time.time() * 1000))
                resp = self.session.get(
                    "https://account.xiaomi.com/longPolling/loginUrl",
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )

                if resp.status_code != 200:
                    if attempt < 4:
                        time.sleep(1)
                        continue
                    raise XiaomiLoginError(f"获取二维码失败: HTTP {resp.status_code}")

                text = resp.text.replace("&&&START&&&", "").strip()
                if not text.startswith("{"):
                    if attempt < 4:
                        time.sleep(1)
                        continue
                    raise XiaomiLoginError(f"获取二维码失败: 响应格式异常")

                data = json.loads(text)
                if "qr" in data:
                    self._qr_image_url = data["qr"]
                    self._login_url = data["loginUrl"]
                    self._long_polling_url = data["lp"]
                    self._timeout = data.get("timeout", 300)
                    return True
                return False
            except (json.JSONDecodeError, requests.exceptions.RequestException) as e:
                if attempt < 4:
                    time.sleep(1)
                else:
                    raise XiaomiLoginError(f"获取二维码失败: {e}")

    def show_qr_code(self) -> None:
        """显示 QR 码给用户扫描"""
        print(f"\n请用小米手机扫描以下二维码登录:")
        print(f"  扫码链接: {self._login_url}\n")

        try:
            import segno
            qr = segno.make(self._login_url)
            qr.terminal(compact=True)
            print()
        except ImportError:
            # 尝试 qrcode 库
            try:
                import qrcode
                qr = qrcode.QRCode(border=1)
                qr.add_data(self._login_url)
                qr.make(fit=True)
                qr.print_ascii(invert=True)
                print()
            except ImportError:
                # 尝试在浏览器中打开
                print("  (终端无法显示二维码，正在尝试在浏览器中打开...)")
                try:
                    qr_api = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={self._login_url}"
                    webbrowser.open(qr_api)
                    print("  如果浏览器没有打开，请手动访问上面的扫码链接。")
                except Exception:
                    print("  请手动在手机浏览器中访问上述链接，或复制到微信扫码。")

    def wait_for_scan(self) -> dict:
        """步骤2: 轮询等待扫码结果"""
        print("等待扫码中...")

        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed > self._timeout:
                raise XiaomiLoginError("扫码超时，请重新运行。")

            remaining = int(self._timeout - elapsed)
            sys.stdout.write(f"\r  剩余时间: {remaining}s  ")
            sys.stdout.flush()

            try:
                resp = self.session.get(
                    self._long_polling_url,
                    timeout=10,
                    allow_redirects=False,
                )
            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException as e:
                raise XiaomiLoginError(f"网络错误: {e}")

            text = resp.text.replace("&&&START&&&", "")
            if not text.strip():
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            # 扫码成功
            if "userId" in data and "ssecurity" in data:
                print("\n扫码成功！")
                return data

            # 检查是否有错误
            code = data.get("code", 0)
            if code != 0:
                raise XiaomiLoginError(f"扫码登录失败 (code={code}): {data.get('description', '')}")

    def get_service_token(self, login_data: dict) -> str:
        """步骤3: 获取 serviceToken 并构建平台 Cookie"""
        location = login_data.get("location", "")
        if not location:
            raise XiaomiLoginError("登录成功但未获取到跳转地址。")

        # 设置初始 Cookie
        self.session.cookies.set("userId", str(login_data.get("userId", "")), domain="xiaomi.com")
        pass_token = login_data.get("passToken", "")
        if pass_token:
            self.session.cookies.set("passToken", pass_token, domain="xiaomi.com")

        # 跟踪重定向
        redirect_cookies = {}
        url = location
        for _ in range(10):
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            new_cookies = parse_set_cookies(resp.headers)
            redirect_cookies.update(new_cookies)
            self.session.cookies.update(new_cookies)

            redirect_url = resp.headers.get("location")
            if redirect_url and resp.status_code in (301, 302, 303, 307):
                url = redirect_url if redirect_url.startswith("http") else \
                    f"https://account.xiaomi.com{redirect_url}"
            else:
                break

        # 构建平台 Cookie
        all_cookies = {**self.session.cookies.get_dict(), **redirect_cookies}
        return build_platform_cookies(redirect_cookies, all_cookies)

    def login(self) -> str:
        """完整扫码登录流程"""
        if not self.get_qr_code():
            raise XiaomiLoginError("获取二维码失败，请检查网络。")

        self.show_qr_code()
        login_data = self.wait_for_scan()
        self._last_login_data = login_data  # 保存供外部读取 passToken 等
        return self.get_service_token(login_data)


# ============================================================
# 登录方式 2: 浏览器 Cookie 自动读取
# ============================================================


def try_browser_cookies() -> str | None:
    """尝试从浏览器读取 MiMo 平台的 Cookie"""
    import sqlite3
    import tempfile
    import shutil

    # 常见浏览器 Cookie 数据库路径 (Windows)
    browser_paths = {}
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")

    if local:
        browser_paths["Chrome"] = Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies"
        browser_paths["Edge"] = Path(local) / "Microsoft" / "Edge" / "User Data" / "Default" / "Network" / "Cookies"
        browser_paths["Chrome_SSO"] = Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Cookies"

    found_browser = None
    cookie_path = None

    for name, path in browser_paths.items():
        if path.exists():
            found_browser = name
            cookie_path = path
            break

    if not cookie_path:
        return None

    print(f"  从 {found_browser} 浏览器读取 Cookie...")

    # 复制数据库（避免锁定问题）
    tmp_path = Path(tempfile.mktemp(suffix=".db"))
    try:
        shutil.copy2(cookie_path, tmp_path)
    except Exception as e:
        print(f"  复制 Cookie 数据库失败: {e}")
        return None

    try:
        conn = sqlite3.connect(str(tmp_path))
        cursor = conn.cursor()

        # 查询 platform.xiaomimimo.com 的 Cookie
        # 注意: Chrome 的 host_key 存储格式是 .domain.com
        cursor.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE '%xiaomimimo.com%' OR host_key LIKE '%mi.com%'"
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("  未找到 MiMo 相关的 Cookie。请先在浏览器中登录 mimo.mi.com")
            return None

        # 构建 Cookie 字典
        cookie_dict = {}
        for name, value in rows:
            if value:
                cookie_dict[name] = value

        # 检查关键 Cookie 是否存在
        critical = ["userId", "serviceToken", "api-platform_serviceToken"]
        missing = [c for c in critical if c not in cookie_dict]

        if missing:
            print(f"  Cookie 不完整，缺少: {', '.join(missing)}")
            print("  请先在浏览器中登录 mimo.mi.com 控制台")
            return None

        return cookie_dict_to_str(cookie_dict)

    except Exception as e:
        print(f"  读取 Cookie 失败: {e}")
        print("  提示: 请关闭浏览器后重试，或使用其他登录方式。")
        return None
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ============================================================
# 登录方式 3: 密码登录
# ============================================================


class XiaomiLoginError(Exception):
    pass


class PasswordLogin:
    """小米账号密码登录"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._sign = ""
        self._device_id = f"{os.urandom(8).hex().upper()}"

    def login(self, username: str, password: str) -> str:
        """完整密码登录流程"""
        # 设置初始 Cookie
        self.session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="mi.com")
        self.session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain="xiaomi.com")
        self.session.cookies.set("deviceId", self._device_id, domain="mi.com")
        self.session.cookies.set("deviceId", self._device_id, domain="xiaomi.com")

        # 步骤1: 获取登录页
        self._service_login()

        # 步骤2: 提交登录
        auth_result = self._login_auth(username, password)

        # 步骤3: 获取 serviceToken 并构建平台 Cookie
        location = auth_result.get("location", "")
        if not location:
            code = auth_result.get("code", -1)
            raise XiaomiLoginError(f"登录失败 (code={code}): {auth_result.get('description', '未知错误')}")

        return self._get_platform_cookies(location)

    def _service_login(self):
        """获取登录页面"""
        params = {
            "sid": MIMO_LOGIN_SID,
            "_json": "true",
            "callback": MIMO_LOGIN_CALLBACK,
        }
        resp = self.session.get(
            f"{XIAOMI_ACCOUNT_BASE}/serviceLogin",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        text = resp.text.replace("&&&START&&&", "")
        data = json.loads(text)

        if "_sign" in data:
            self._sign = data["_sign"]
        elif "ssecurity" in data:
            # 已经登录
            raise XiaomiLoginError("already_logged_in")

    def _login_auth(self, username: str, password: str) -> dict:
        """提交登录认证"""
        pass_hash = hashlib.md5(password.encode("utf-8")).hexdigest().upper()

        fields = {
            "sid": MIMO_LOGIN_SID,
            "hash": pass_hash,
            "callback": MIMO_LOGIN_CALLBACK,
            "qs": "%3Fsid%3D" + MIMO_LOGIN_SID + "%26_json%3Dtrue",
            "user": username,
            "_sign": self._sign,
            "_json": "true",
        }

        resp = self.session.post(
            f"{XIAOMI_ACCOUNT_BASE}/serviceLoginAuth2",
            params=fields,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        text = resp.text.replace("&&&START&&&", "")
        data = json.loads(text)

        # 需要验证码
        if "captchaUrl" in data and data["captchaUrl"]:
            raise XiaomiLoginError(
                "需要验证码。请使用扫码登录或手动 Cookie 方式。\n"
                f"  验证码地址: {data['captchaUrl']}"
            )

        # 需要 2FA
        if "notificationUrl" in data:
            raise XiaomiLoginError(
                "需要邮箱/手机验证码验证。\n"
                "  请使用扫码登录或手动 Cookie 方式。"
            )

        return data

    def _get_platform_cookies(self, location: str) -> str:
        """跟踪重定向获取平台 Cookie"""
        redirect_cookies = {}
        url = location
        for _ in range(10):
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            new_cookies = parse_set_cookies(resp.headers)
            redirect_cookies.update(new_cookies)
            self.session.cookies.update(new_cookies)

            redirect_url = resp.headers.get("location")
            if redirect_url and resp.status_code in (301, 302, 303, 307):
                url = redirect_url if redirect_url.startswith("http") else \
                    f"https://account.xiaomi.com{redirect_url}"
            else:
                break

        all_cookies = {**self.session.cookies.get_dict(), **redirect_cookies}
        return build_platform_cookies(redirect_cookies, all_cookies)


# ============================================================
# Cookie 缓存
# ============================================================


def save_cookies(cookie_str: str, method: str = "", extra: dict = None):
    """保存 Cookie 和登录信息到文件"""
    data = {
        "cookie": cookie_str,
        "method": method,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        data.update(extra)
    COOKIES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cookie 已保存到 {COOKIES_FILE}")


def load_cookies() -> dict:
    """从文件加载 Cookie 和登录信息"""
    if not COOKIES_FILE.exists():
        return {}
    try:
        return json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return {}


# ============================================================
# Cookie 自动刷新（基于 passToken）
# ============================================================


def _extract_cookie_field(cookie_str: str, name: str) -> str:
    """从 cookie 字符串中提取指定字段的值"""
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith(f"{name}="):
            val = part[len(name) + 1:]
            # 去掉可能的引号
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            return val
    return ""


def refresh_mimo_cookie(cookie_str: str) -> str | None:
    """
    用 passToken 刷新 MiMo 平台的 serviceToken。
    返回新的 cookie 字符串，失败返回 None。

    流程（参考 mijiaAPI_V2 + MiMo STS 机制）：
    1. 先探测获取 loginUrl（包含正确的 callback + sign）
    2. 用 passToken 访问 serviceLogin（带 callback 参数）
    3. 跟随 STS 重定向获取新的 api-platform_serviceToken
    4. 构建新 cookie 字符串
    """
    pass_token = _extract_cookie_field(cookie_str, "passToken")
    user_id = _extract_cookie_field(cookie_str, "userId")

    if not pass_token or not user_id:
        print("[MiMo] 缺少 passToken 或 userId，无法自动刷新", flush=True)
        return None

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        # Step 0: 探测获取 loginUrl（从 401 响应中提取 callback）
        test_api = MiMoAPI(cookie_str)
        test_resp = test_api.get_user_profile()
        login_url = test_resp.get("loginUrl", "")

        if not login_url:
            print("[MiMo] 无法获取 loginUrl", flush=True)
            return None

        # 从 loginUrl 中提取 callback 参数
        parsed = urllib.parse.urlparse(login_url)
        login_params = urllib.parse.parse_qs(parsed.query)
        callback = login_params.get("callback", [""])[0]

        if not callback:
            print("[MiMo] loginUrl 中无 callback", flush=True)
            return None

        # Step 1: 设置 passToken cookie，请求 serviceLogin（带 callback）
        session.cookies.set("passToken", pass_token, domain="xiaomi.com")
        session.cookies.set("userId", user_id, domain="xiaomi.com")

        params = {
            "callback": callback,
            "sid": MIMO_LOGIN_SID,
            "_json": "true",
            "_group": "DEFAULT",
        }
        resp = session.get(
            "https://account.xiaomi.com/pass/serviceLogin",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        text = resp.text.replace("&&&START&&&", "").strip()
        data = json.loads(text)

        if data.get("code") != 0:
            print(f"[MiMo] passToken 刷新失败 (code={data.get('code')}): {data.get('desc', '')}", flush=True)
            return None

        location = data.get("location", "")
        if not location:
            print("[MiMo] passToken 刷新失败: 无 location", flush=True)
            return None

        # Step 2: 跟随 location → STS 重定向获取新 serviceToken
        redirect_cookies = {}
        url = location
        for _ in range(15):
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            new_cookies = parse_set_cookies(resp.headers)
            redirect_cookies.update(new_cookies)
            session.cookies.update(new_cookies)

            redirect_url = resp.headers.get("location")
            if redirect_url and resp.status_code in (301, 302, 303, 307):
                url = redirect_url if redirect_url.startswith("http") else \
                    f"https://account.xiaomi.com{redirect_url}"
            else:
                break

        # Step 3: 构建新 cookie
        all_cookies = {**session.cookies.get_dict(), **redirect_cookies}
        new_cookie = build_platform_cookies(redirect_cookies, all_cookies)

        if "api-platform_serviceToken" not in new_cookie and "serviceToken" not in new_cookie:
            print("[MiMo] 刷新后未获取到 serviceToken", flush=True)
            return None

        print("[MiMo] Cookie 刷新成功 [OK]", flush=True)
        return new_cookie

    except Exception as e:
        print(f"[MiMo] Cookie 刷新异常: {e}", flush=True)
        return None


# ============================================================
# 旧的 re_login（向后兼容）
# ============================================================


def re_login(cache_info: dict) -> str | None:
    """
    用缓存的登录信息重新获取 Cookie。
    - browser: 重新从浏览器 Cookie 数据库读取
    - password: 用缓存的账号密码重新登录
    - qr / manual: 无法自动刷新，返回 None
    """
    method = cache_info.get("method", "")

    if method == "browser":
        print("  重新从浏览器读取 Cookie...")
        return try_browser_cookies()

    if method == "password":
        username = cache_info.get("username", "")
        password = cache_info.get("password", "")
        if username and password:
            print(f"  重新登录 (账号: {username})...")
            try:
                pl = PasswordLogin()
                return pl.login(username, password)
            except XiaomiLoginError as e:
                print(f"  重新登录失败: {e}")
        else:
            print("  缺少缓存的登录凭据，无法自动重新登录。")

    return None


def get_cookie_with_refresh(cookie_str: str, cache_info: dict) -> str:
    """
    先探测 Cookie 是否有效，如果 401 则自动重新获取。
    支持 browser / password / qr（passToken 刷新）方式。
    """
    api = MiMoAPI(cookie_str)
    test = api.get_user_profile()
    if test.get("code") != 401:
        return cookie_str  # Cookie 仍然有效

    method = cache_info.get("method", "")

    # 所有方式都先尝试 passToken 刷新
    print("Cookie 已过期，尝试 passToken 刷新...")
    new_cookie = refresh_mimo_cookie(cookie_str)
    if new_cookie:
        print("  passToken 刷新成功！")
        save_cookies(new_cookie, method, cache_info)
        return new_cookie

    # passToken 刷新失败，browser/password 方式可以重登
    if method not in ("browser", "password"):
        print("passToken 刷新失败，请重新登录。")
        print("  运行: python mimo_usage.py --login qr --save")
        sys.exit(1)

    print("Cookie 已过期，尝试自动重新获取...")
    new_cookie = re_login(cache_info)
    if new_cookie:
        print("  刷新成功！")
        save_cookies(new_cookie, method, cache_info)
        return new_cookie

    print("\n自动刷新失败。请手动重新登录:")
    print("  python mimo_usage.py --no-cache")
    sys.exit(1)


# ============================================================
# MiMo API 查询
# ============================================================


class MiMoAPI:
    """MiMo 平台 API 客户端"""

    def __init__(self, cookie_str: str):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en",
            "Content-Type": "application/json",
            "x-timezone": "Asia/Shanghai",
            "Cookie": cookie_str,
        })
        # 从 Cookie 中提取 api-platform_ph（部分接口需要作为 query 参数传）
        self._ph = ""
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip() == "api-platform_ph":
                    self._ph = v.strip().strip('"')

    def _get(self, endpoint: str) -> dict:
        url = f"{MIMO_PLATFORM_BASE}/{endpoint}"
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        return resp.json()

    def get_user_profile(self) -> dict:
        return self._get("userProfile")

    def get_token_plan_detail(self) -> dict:
        return self._get("tokenPlan/detail")

    def get_token_plan_usage(self) -> dict:
        return self._get("tokenPlan/usage")

    def get_balance(self) -> dict:
        return self._get("balance")

    def get_token_plan_usage_detail(self, year: int = None) -> dict:
        """Token Plan 按月按模型的用量明细（需要 api-platform_ph 参数）"""
        if year is None:
            year = time.localtime().tm_year
        url = f"{MIMO_PLATFORM_BASE}/usage/token-plan/list"
        params = {}
        if self._ph:
            params["api-platform_ph"] = self._ph
        resp = self.session.post(
            url, params=params, json={"year": year}, timeout=REQUEST_TIMEOUT,
        )
        return resp.json()

    def get_usage(self) -> dict:
        """按量付费用量统计（token、费用、请求次数、限流）"""
        return self._get("usage")

    def get_usage_detail(self, year: int = None) -> dict:
        """按量付费月度明细（按模型拆分）"""
        if year is None:
            year = time.localtime().tm_year
        return self._get(f"usage/detail?year={year}")


# ============================================================
# 结果展示
# ============================================================


def format_credits(num: float) -> str:
    """将数字格式化为可读的额度数量"""
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(int(num))


def make_progress_bar(pct: float, width: int = 20) -> str:
    """生成进度条字符串（使用 ASCII 字符避免 Windows GBK 编码问题）"""
    filled = int(pct / 100 * width)
    return "#" * filled + "-" * (width - filled)


def display_rich(profile: dict, plan: dict, usage: dict, balance: dict = None,
                 payg_usage: dict = None, usage_detail: dict = None,
                 tp_usage_detail: dict = None):
    """使用 rich 库美化输出"""
    console = Console()

    # 用户信息
    ud = profile.get("data", {})
    if ud:
        t = Table(box=box.ROUNDED, show_header=False, title="[bold cyan]用户信息[/]")
        t.add_column("字段", style="dim")
        t.add_column("值")
        t.add_row("用户ID", str(ud.get("userId", "N/A")))
        t.add_row("邮箱", ud.get("email") or ud.get("platformEmail") or "N/A")
        t.add_row("昵称", ud.get("nickName") or "N/A")
        t.add_row("手机", ud.get("phone") or "N/A")
        console.print(t)
        console.print()

    # 余额信息
    bd = balance.get("data", {}) if balance else {}
    if bd:
        currency = bd.get("currency", "CNY")
        t = Table(box=box.ROUNDED, show_header=False, title="[bold yellow]账户余额[/]")
        t.add_column("字段", style="dim")
        t.add_column("值")
        t.add_row("余额", f"{bd.get('balance', '0.00')} {currency}")
        t.add_row("现金余额", f"{bd.get('cashBalance', '0.00')} {currency}")
        gift = bd.get("giftBalance", "0.00")
        if float(gift) > 0:
            t.add_row("赠送余额", f"{gift} {currency}")
        frozen = bd.get("frozenBalance", "0.00")
        if float(frozen) > 0:
            t.add_row("冻结余额", f"{frozen} {currency}")
        console.print(t)
        console.print()

    # 套餐详情
    pd = plan.get("data", {})
    if pd:
        t = Table(box=box.ROUNDED, show_header=False, title="[bold green]Token Plan 套餐[/]")
        t.add_column("字段", style="dim")
        t.add_column("值")
        t.add_row("套餐名称", pd.get("planName", "N/A"))
        t.add_row("套餐代码", pd.get("planCode", "N/A"))
        t.add_row("到期时间", pd.get("currentPeriodEnd", "N/A"))
        t.add_row("是否过期", "是" if pd.get("expired") else "否")
        auto = "已开启" if pd.get("enableAutoRenew") else "未开启"
        t.add_row("自动续费", auto)
        console.print(t)
    else:
        console.print(Panel("[yellow]未找到 Token Plan 套餐信息[/]", title="套餐状态"))
    console.print()

    # Token Plan 用量
    ud = usage.get("data", {})
    if ud:
        for label, key in [("当月用量", "monthUsage"), ("总用量", "usage")]:
            group = ud.get(key, {})
            items = group.get("items", [])
            if not items:
                continue

            overall_pct = group.get("percent", 0)
            console.print(f"[bold]{label}[/] (总使用率: {overall_pct:.1f}%)")

            t = Table(box=box.SIMPLE_HEAVY)
            t.add_column("项目", style="cyan")
            t.add_column("已使用", justify="right")
            t.add_column("总额度", justify="right")
            t.add_column("使用率", justify="right")
            t.add_column("进度", min_width=24)

            for item in items:
                used = format_credits(item.get("used", 0))
                limit = format_credits(item.get("limit", 0))
                pct = item.get("percent", 0)
                color = "red" if pct >= 90 else "yellow" if pct >= 70 else "green"
                bar = f"[{color}]{make_progress_bar(pct)}[/]"
                t.add_row(item.get("name", "N/A"), used, limit, f"{pct:.1f}%", bar)

            console.print(t)
            console.print()

    # Token Plan 按模型用量明细
    tp_data = tp_usage_detail.get("data", []) if tp_usage_detail else []
    if tp_data:
        console.print("[bold]Token Plan 按模型用量明细[/]")
        t = Table(box=box.SIMPLE_HEAVY)
        t.add_column("月份", style="cyan")
        t.add_column("模型")
        t.add_column("Total", justify="right")
        t.add_column("Input(命中)", justify="right")
        t.add_column("Input(未命中)", justify="right")
        t.add_column("Output", justify="right")
        t.add_column("请求次数", justify="right")
        for item in tp_data:
            t.add_row(
                str(item.get("date", "")),
                item.get("model", ""),
                format_credits(item.get("totalToken", 0)),
                format_credits(item.get("inputHitToken", 0)),
                format_credits(item.get("inputMissToken", 0)),
                format_credits(item.get("outputToken", 0)),
                format_credits(item.get("requestCount", 0)),
            )
        console.print(t)
        console.print()

    # 按量付费用量统计
    pg = payg_usage.get("data", {}) if payg_usage else {}
    if pg:
        tu = pg.get("tokenUsage", {})
        cu = pg.get("costUsage", {})
        ar = pg.get("accountRateLimit", {})

        if tu or cu:
            t = Table(box=box.ROUNDED, show_header=False, title="[bold magenta]按量付费统计[/]")
            t.add_column("字段", style="dim")
            t.add_column("值")
            if tu:
                t.add_row("总 Token", format_credits(tu.get("totalToken", 0)))
                t.add_row("Input Token", format_credits(tu.get("inputToken", 0)))
                t.add_row("Output Token", format_credits(tu.get("outputToken", 0)))
                t.add_row("Cache Token", format_credits(tu.get("cacheToken", 0)))
            if cu:
                t.add_row("累计费用", f"{cu.get('totalCost', '0.00')} CNY")
                t.add_row("本月费用", f"{cu.get('currentMonthCost', '0.00')} CNY")
            if ar:
                t.add_row("TPM 限制", format_credits(ar.get("tpm", 0)))
                t.add_row("RPM 限制", str(ar.get("rpm", 0)))
                t.add_row("并发限制", str(ar.get("concurrency", 0)))
            console.print(t)
            console.print()

    # 按量付费月度明细（按模型拆分）
    udd = usage_detail.get("data", {}) if usage_detail else {}
    if udd:
        model_tokens = udd.get("modelTokenUsage", [])
        model_requests = udd.get("modelRequests", [])
        monthly_requests = udd.get("requests", [])

        # 月度总请求次数
        if monthly_requests:
            console.print("[bold]按量付费月度请求次数[/]")
            t = Table(box=box.SIMPLE_HEAVY)
            t.add_column("月份", style="cyan")
            t.add_column("请求次数", justify="right")
            for row in monthly_requests:
                t.add_row(str(row[0]), format_credits(row[1]))
            console.print(t)
            console.print()

        # 按模型拆分的 token 用量
        if model_tokens:
            console.print("[bold]按模型拆分的 Token 用量[/]")
            t = Table(box=box.SIMPLE_HEAVY)
            t.add_column("模型", style="cyan")
            t.add_column("月份")
            t.add_column("Input", justify="right")
            t.add_column("Output", justify="right")
            t.add_column("Total", justify="right")
            t.add_column("Cache", justify="right")
            for mt in model_tokens:
                model = mt.get("model", "?")
                for detail in mt.get("usageDetail", []):
                    t.add_row(
                        model,
                        str(detail[0]),
                        format_credits(detail[1]),
                        format_credits(detail[2]),
                        format_credits(detail[3]),
                        format_credits(detail[4]),
                    )
            console.print(t)
            console.print()

        # 按模型拆分的请求次数
        if model_requests:
            console.print("[bold]按模型拆分的请求次数[/]")
            t = Table(box=box.SIMPLE_HEAVY)
            t.add_column("模型", style="cyan")
            t.add_column("月份")
            t.add_column("请求次数", justify="right")
            for mr in model_requests:
                model = mr.get("model", "?")
                for detail in mr.get("requestsDetail", []):
                    t.add_row(model, str(detail[0]), format_credits(detail[1]))
            console.print(t)
            console.print()


def display_plain(profile: dict, plan: dict, usage: dict, balance: dict = None,
                  payg_usage: dict = None, usage_detail: dict = None,
                  tp_usage_detail: dict = None):
    """纯文本输出"""
    print("=" * 55)
    print("  MiMo 使用情况")
    print("=" * 55)

    ud = profile.get("data", {})
    if ud:
        print(f"\n--- 用户信息 ---")
        print(f"  用户ID:  {ud.get('userId', 'N/A')}")
        print(f"  邮箱:    {ud.get('email') or ud.get('platformEmail') or 'N/A'}")
        print(f"  昵称:    {ud.get('nickName') or 'N/A'}")

    bd = balance.get("data", {}) if balance else {}
    if bd:
        currency = bd.get("currency", "CNY")
        print(f"\n--- 账户余额 ---")
        print(f"  余额:    {bd.get('balance', '0.00')} {currency}")
        print(f"  现金余额: {bd.get('cashBalance', '0.00')} {currency}")
        gift = bd.get("giftBalance", "0.00")
        if float(gift) > 0:
            print(f"  赠送余额: {gift} {currency}")

    pd = plan.get("data", {})
    print(f"\n--- Token Plan ---")
    if pd:
        print(f"  套餐:    {pd.get('planName', 'N/A')}")
        print(f"  到期:    {pd.get('currentPeriodEnd', 'N/A')}")
        print(f"  过期:    {'是' if pd.get('expired') else '否'}")
        print(f"  自动续费: {'已开启' if pd.get('enableAutoRenew') else '未开启'}")
    else:
        print("  未找到套餐信息")

    ud = usage.get("data", {})
    if ud:
        for label, key in [("当月用量", "monthUsage"), ("总用量", "usage")]:
            group = ud.get(key, {})
            items = group.get("items", [])
            if not items:
                continue
            print(f"\n  [{label}] (总使用率: {group.get('percent', 0):.1f}%)")
            for item in items:
                name = item.get("name", "N/A")
                used = format_credits(item.get("used", 0))
                limit = format_credits(item.get("limit", 0))
                pct = item.get("percent", 0)
                bar = make_progress_bar(pct, 15)
                print(f"    {name:22s} {used:>10s} / {limit:>10s}  {pct:5.1f}%  {bar}")

    # Token Plan 按模型明细
    tp_data = tp_usage_detail.get("data", []) if tp_usage_detail else []
    if tp_data:
        print(f"\n--- Token Plan 按模型用量明细 ---")
        for item in tp_data:
            model = item.get("model", "?")
            date = item.get("date", "")
            print(f"  {model:20s} {date}  total={format_credits(item.get('totalToken', 0)):>10s}  "
                  f"hit={format_credits(item.get('inputHitToken', 0)):>10s}  "
                  f"miss={format_credits(item.get('inputMissToken', 0)):>8s}  "
                  f"out={format_credits(item.get('outputToken', 0)):>8s}  "
                  f"req={format_credits(item.get('requestCount', 0)):>6s}")

    # 按量付费统计
    pg = payg_usage.get("data", {}) if payg_usage else {}
    if pg:
        tu = pg.get("tokenUsage", {})
        cu = pg.get("costUsage", {})
        if tu or cu:
            print(f"\n--- 按量付费统计 ---")
            if tu:
                print(f"  总 Token:    {format_credits(tu.get('totalToken', 0))}")
                print(f"  Input:       {format_credits(tu.get('inputToken', 0))}")
                print(f"  Output:      {format_credits(tu.get('outputToken', 0))}")
                print(f"  Cache:       {format_credits(tu.get('cacheToken', 0))}")
            if cu:
                print(f"  累计费用:    {cu.get('totalCost', '0.00')} CNY")
                print(f"  本月费用:    {cu.get('currentMonthCost', '0.00')} CNY")

    # 按模型拆分
    udd = usage_detail.get("data", {}) if usage_detail else {}
    if udd:
        model_tokens = udd.get("modelTokenUsage", [])
        model_requests = udd.get("modelRequests", [])
        if model_tokens:
            print(f"\n--- 按模型 Token 用量 ---")
            for mt in model_tokens:
                model = mt.get("model", "?")
                for d in mt.get("usageDetail", []):
                    print(f"  {model:20s} {d[0]}  in={format_credits(d[1]):>8s}  out={format_credits(d[2]):>8s}  total={format_credits(d[3]):>8s}")
        if model_requests:
            print(f"\n--- 按模型请求次数 ---")
            for mr in model_requests:
                model = mr.get("model", "?")
                for d in mr.get("requestsDetail", []):
                    print(f"  {model:20s} {d[0]}  {d[1]} 次")

    print()


# ============================================================
# 登录选择交互
# ============================================================


def get_cookie_interactively() -> tuple:
    """交互式选择登录方式，返回 (cookie_str, method_name)"""

    methods = {
        "1": ("扫码登录", "qr"),
        "2": ("浏览器 Cookie (自动从 Chrome/Edge 读取)", "browser"),
        "3": ("密码登录 (邮箱/手机号 + 密码)", "password"),
        "4": ("手动输入 Cookie", "manual"),
    }

    print("\n请选择登录方式:")
    for k, (desc, _) in methods.items():
        print(f"  {k}. {desc}")

    choice = input("\n请输入选项 (1-4, 默认 1): ").strip() or "1"

    if choice not in methods:
        print("无效选项")
        sys.exit(1)

    _, method = methods[choice]

    # --- 扫码登录 ---
    if method == "qr":
        try:
            qr = QRCodeLogin()
            cookie_str = qr.login()
            extra = {}
            if qr._last_login_data:
                ld = qr._last_login_data
                extra = {
                    "passToken": ld.get("passToken", ""),
                    "userId": str(ld.get("userId", "")),
                    "ssecurity": ld.get("ssecurity", ""),
                }
            return cookie_str, "qr", extra
        except XiaomiLoginError as e:
            print(f"\n扫码登录失败: {e}")
            sys.exit(1)

    # --- 浏览器 Cookie ---
    if method == "browser":
        cookie_str = try_browser_cookies()
        if cookie_str:
            return cookie_str, "browser", {}
        print("\n自动读取失败。请尝试其他登录方式。")
        sys.exit(1)

    # --- 密码登录 ---
    if method == "password":
        username = input("请输入小米账号 (邮箱/手机号/用户名): ").strip()
        if not username:
            print("账号不能为空")
            sys.exit(1)
        import getpass
        password = getpass.getpass("请输入密码 (不显示): ")
        if not password:
            print("密码不能为空")
            sys.exit(1)

        try:
            pl = PasswordLogin()
            cookie_str = pl.login(username, password)
            return cookie_str, "password", {}
        except XiaomiLoginError as e:
            print(f"\n登录失败: {e}")
            sys.exit(1)

    # --- 手动 Cookie ---
    if method == "manual":
        print("\n请从浏览器获取 Cookie:")
        print("  1. 打开 https://mimo.mi.com 并登录")
        print("  2. 按 F12 → Network → 刷新页面 → 点击任意请求")
        print("  3. 在 Request Headers 中找到 Cookie 字段并复制")
        print()
        cookie_str = input("请粘贴 Cookie: ").strip()
        if not cookie_str:
            print("Cookie 不能为空")
            sys.exit(1)
        return cookie_str, "manual"


# ============================================================
# 主程序
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="MiMo Token Plan 使用情况查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python mimo_usage.py                    交互式选择登录方式
  python mimo_usage.py --login qr         扫码登录（推荐）
  python mimo_usage.py --login browser    自动从浏览器读取 Cookie
  python mimo_usage.py --login password   密码登录
  python mimo_usage.py --cookie FILE      从文件加载 Cookie
  python mimo_usage.py --json             JSON 格式输出
  python mimo_usage.py --save             登录后保存 Cookie（支持自动刷新）

Cookie 自动刷新:
  使用 --save 保存 Cookie 后，脚本会同时保存登录方式。
  下次运行时如果 Cookie 过期:
    - browser 模式: 自动重新从浏览器读取 Cookie
    - password 模式: 自动用缓存的账号密码重新登录
    - qr / manual 模式: 需要手动重新登录

依赖安装:
  pip install requests rich        # 基础依赖
  pip install segno                # 终端显示二维码（可选）
  pip install qrcode               # 备选二维码显示（可选）
        """,
    )
    parser.add_argument("--login", "-l", choices=["qr", "browser", "password"],
                        help="登录方式: qr=扫码, browser=浏览器Cookie, password=密码")
    parser.add_argument("--cookie", "-c", help="从文件加载 Cookie")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 格式输出")
    parser.add_argument("--save", "-s", action="store_true", help="登录后保存 Cookie（支持自动刷新）")
    parser.add_argument("--no-cache", action="store_true", help="不使用缓存的 Cookie")
    args = parser.parse_args()

    cookie_str = None
    cache_info = {}
    extra_save = {}  # 额外需要保存的信息（如密码登录的账号密码）

    # 1. 从文件加载 Cookie（手动指定，不走自动刷新）
    if args.cookie:
        cookie_path = Path(args.cookie)
        if cookie_path.exists():
            content = cookie_path.read_text(encoding="utf-8").strip()
            if content.startswith("{"):
                try:
                    data = json.loads(content)
                    cookie_str = data.get("cookie", content)
                except json.JSONDecodeError:
                    cookie_str = content
            else:
                cookie_str = content
        else:
            print(f"错误: Cookie 文件不存在: {args.cookie}")
            sys.exit(1)

    # 2. 从缓存加载
    if not cookie_str and not args.no_cache:
        cache_info = load_cookies()
        cookie_str = cache_info.get("cookie")
        if cookie_str:
            method = cache_info.get("method", "未知")
            print(f"使用缓存的 Cookie (登录方式: {method})")

    # 3. 自动登录
    if not cookie_str:
        if args.login == "qr":
            try:
                qr = QRCodeLogin()
                cookie_str = qr.login()
                cache_info = {"method": "qr"}
                # 保存 passToken 等字段，供自动刷新使用
                if qr._last_login_data:
                    ld = qr._last_login_data
                    extra_save = {
                        "passToken": ld.get("passToken", ""),
                        "userId": str(ld.get("userId", "")),
                        "ssecurity": ld.get("ssecurity", ""),
                    }
            except XiaomiLoginError as e:
                print(f"扫码登录失败: {e}")
                sys.exit(1)
        elif args.login == "browser":
            cookie_str = try_browser_cookies()
            if not cookie_str:
                sys.exit(1)
            cache_info = {"method": "browser"}
        elif args.login == "password":
            username = input("小米账号 (邮箱/手机号): ").strip()
            import getpass
            password = getpass.getpass("密码: ")
            try:
                pl = PasswordLogin()
                cookie_str = pl.login(username, password)
                cache_info = {"method": "password"}
                extra_save = {"username": username, "password": password}
            except XiaomiLoginError as e:
                print(f"登录失败: {e}")
                sys.exit(1)
        else:
            # 交互式选择
            cookie_str, method, extra_save = get_cookie_interactively()
            cache_info = {"method": method}

        # 默认保存 Cookie（支持自动刷新）
        if cookie_str:
            save_data = {**cache_info, **extra_save}
            save_cookies(cookie_str, cache_info.get("method", ""), save_data)

    # 4. 查询 API（带自动刷新）
    print("\n正在查询 MiMo Token Plan 使用情况...\n")

    # 如果有缓存信息，支持自动刷新
    if cache_info.get("method") and cache_info["method"] not in ("manual",):
        cookie_str = get_cookie_with_refresh(cookie_str, cache_info)

    api = MiMoAPI(cookie_str)

    try:
        profile = api.get_user_profile()
        plan = api.get_token_plan_detail()
        usage = api.get_token_plan_usage()
        balance = api.get_balance()
        payg_usage = api.get_usage()
        usage_detail = api.get_usage_detail()
        tp_usage_detail = api.get_token_plan_usage_detail()

        if args.json:
            result = {
                "profile": profile.get("data"),
                "plan": plan.get("data"),
                "usage": usage.get("data"),
                "balance": balance.get("data"),
                "payg_usage": payg_usage.get("data"),
                "usage_detail": usage_detail.get("data"),
                "tp_usage_detail": tp_usage_detail.get("data"),
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif HAS_RICH:
            display_rich(profile, plan, usage, balance, payg_usage, usage_detail, tp_usage_detail)
        else:
            display_plain(profile, plan, usage, balance, payg_usage, usage_detail, tp_usage_detail)

    except requests.exceptions.RequestException as e:
        print(f"网络请求失败: {e}")
        sys.exit(1)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"数据解析失败: {e}")
        print("可能是 Cookie 已过期，请重新登录: python mimo_usage.py --no-cache")
        sys.exit(1)


if __name__ == "__main__":
    main()
