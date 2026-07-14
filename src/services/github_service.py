"""GitHub contribution heatmap fetching and cache handling."""

from __future__ import annotations

import logging

import json
import re
import time
from pathlib import Path

import requests

from core.cache import TTLCache
from core.config import DATA_DIR, load_config

logger = logging.getLogger("cuckoo.github")

GITHUB_USER = "cuckoo711"
GITHUB_CACHE_TTL = 600
GITHUB_DISK_CACHE = DATA_DIR / "github_cache.json"
GITHUB_DISK_CACHE_TTL = 86400

_cache = TTLCache(GITHUB_CACHE_TTL)
_last_error: str | None = None
_last_success_at: float | None = None
_use_api: bool = False  # True 当 token 可用时


def _get_token() -> str | None:
    """从热重载配置读取 GitHub Personal Access Token。"""
    token = load_config().get("github_token")
    return token if isinstance(token, str) and token else None


def reload_config() -> None:
    """清理 GitHub 数据和配置相关状态。"""
    global _last_error, _last_success_at, _use_api
    _cache.clear()
    _last_error = None
    _last_success_at = None
    _use_api = False


def _github_payload(contributions: dict, *, stale: bool = False, error: str | None = None, estimated: bool = True) -> dict:
    return {
        "user": GITHUB_USER,
        "contributions": contributions,
        "estimated": estimated,
        "stale": stale,
        "error": error,
    }


def _read_disk_cache(max_age: float | None = GITHUB_DISK_CACHE_TTL) -> dict | None:
    if not GITHUB_DISK_CACHE.exists():
        return None
    try:
        disk = json.loads(GITHUB_DISK_CACHE.read_text(encoding="utf-8"))
        data = disk.get("data")
        ts = float(disk.get("ts", 0))
        if isinstance(data, dict) and (max_age is None or time.time() - ts < max_age):
            return data
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return None


def _write_disk_cache(contributions: dict):
    try:
        GITHUB_DISK_CACHE.write_text(
            json.dumps({"data": contributions, "ts": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _fetch_from_github_api(token: str) -> dict:
    """通过 GitHub GraphQL API 获取精确贡献数据。"""
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    resp = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"login": GITHUB_USER}},
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub API 返回 {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GitHub API error: {data['errors'][0].get('message', '')}")

    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    contributions = {}
    for week in weeks:
        for day in week["contributionDays"]:
            count = day["contributionCount"]
            if count > 0:
                contributions[day["date"]] = count
    return contributions


def _fetch_from_github() -> dict:
    resp = requests.get(
        f"https://github.com/{GITHUB_USER}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub profile 返回 {resp.status_code}")

    frag_m = re.search(
        r'src="(/[^"]*?controller=profiles[^"]*?tab=contributions[^"]*?)"',
        resp.text,
    )
    if not frag_m:
        raise RuntimeError("未找到 contributions fragment URL")

    frag_url = "https://github.com" + frag_m.group(1).replace("&amp;", "&")
    frag_resp = requests.get(
        frag_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15,
    )
    if frag_resp.status_code != 200:
        raise RuntimeError(f"GitHub fragment 返回 {frag_resp.status_code}")

    rects = re.findall(r'data-date="([^"]+)"[^>]*data-level="([^"]+)"', frag_resp.text)
    level_map = {"0": 0, "1": 2, "2": 5, "3": 8, "4": 12}
    contributions = {}
    for date, level in rects:
        count = level_map.get(level, 0)
        if count > 0:
            contributions[date] = count
    return contributions


def get_github_data() -> dict:
    """Return GitHub contribution data plus status fields.
    优先使用 GraphQL API（需配置 github_token），回退到网页爬取。
    """
    global _last_error, _last_success_at, _use_api

    cached = _cache.get()
    if cached:
        return _github_payload(cached, error=_last_error, estimated=not _use_api)

    disk_fresh = _read_disk_cache(max_age=GITHUB_DISK_CACHE_TTL)
    if disk_fresh is not None:
        _cache.set(disk_fresh)
        _last_success_at = time.time()
        logger.info(f"GitHub: 从磁盘缓存恢复 {len(disk_fresh)} 天数据")
        return _github_payload(disk_fresh, estimated=not _use_api)

    token = _get_token()
    _use_api = bool(token)

    for attempt in range(3):
        try:
            if _use_api:
                contributions = _fetch_from_github_api(token)
                logger.info(f"GitHub API: fetched {len(contributions)} days of contributions")
            else:
                contributions = _fetch_from_github()
                logger.info(f"GitHub scrape: fetched {len(contributions)} days of contributions")
            _last_error = None
            _last_success_at = time.time()
            _cache.set(contributions)
            _write_disk_cache(contributions)
            return _github_payload(contributions, estimated=not _use_api)
        except Exception as e:
            _last_error = str(e)
            logger.error(f"GitHub fetch attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(2)

    logger.error("GitHub: 所有重试均失败")
    stale = _cache.data if isinstance(_cache.data, dict) else None
    if stale is None:
        stale = _read_disk_cache(max_age=None)
    return _github_payload(stale or {}, stale=bool(stale), error=_last_error, estimated=not _use_api)


def get_github_status() -> dict:
    """Return cached GitHub status without performing network requests."""
    has_data = isinstance(_cache.data, dict) and bool(_cache.data)
    cache_age = time.time() - _cache.ts if _cache.ts else None
    stale = bool(has_data and cache_age is not None and cache_age >= GITHUB_CACHE_TTL)
    if _last_error and has_data:
        status = "stale"
        stale = True
    elif _last_error:
        status = "error"
    elif has_data:
        status = "stale" if stale else "ok"
    elif GITHUB_DISK_CACHE.exists():
        status = "unknown"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "stale": stale,
        "error": _last_error,
        "last_success_at": _last_success_at,
        "details": {"estimated": not _use_api, "cached_days": len(_cache.data or {})},
    }


def fetch_github_contributions() -> dict:
    """Backward-compatible API: only return date -> estimated count."""
    return get_github_data().get("contributions", {})
