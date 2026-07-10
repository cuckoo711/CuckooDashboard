"""SMTC media state and lyrics service (Netease + QQ Music)."""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess as _media_sp
import threading
import time
from pathlib import Path

import requests as _requests

from services.config import CONFIG_DIR, SRC_DIR, PROJECT_ROOT

logger = logging.getLogger("cuckoo.media")

_SONG_ID_FILE = CONFIG_DIR / "song_id_overrides.json"

# ============================================================
# SMTC Worker
# ============================================================

_smtc_result = {"status": "idle", "title": "", "artist": "", "progress_ratio": None}
_smtc_lock = threading.Lock()
_smtc_last_update = 0.0
_SMTC_WORKER = str(SRC_DIR / "smtc_worker.py")
_SMTC_PYTHON = str(PROJECT_ROOT / "venv" / "Scripts" / "python.exe")
_smtc_started = False


def _smtc_reader_loop():
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
            proc.wait(timeout=1)
        except Exception as e:
            logger.error(f"[media] worker error: {e}")
        finally:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        _time.sleep(2)


def _ensure_smtc_thread():
    global _smtc_started
    if not _smtc_started:
        _smtc_started = True
        t = threading.Thread(target=_smtc_reader_loop, daemon=True)
        t.start()


# ============================================================
# Song ID Persistence
# ============================================================


def _load_song_id_overrides() -> dict:
    if not _SONG_ID_FILE.exists():
        return {}
    try:
        data = json.loads(_SONG_ID_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_song_id_override(title: str, artist: str, song_id: int):
    overrides = _load_song_id_overrides()
    key = f"{title}|||{artist}"
    overrides[key] = {"song_id": song_id}
    try:
        _SONG_ID_FILE.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error(f"[media] failed to save song_id override: {e}")


# ============================================================
# LRC Parsing
# ============================================================


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


# ============================================================
# Matching Helpers
# ============================================================

_LYRIC_JUNK_KW = (
    "DJ", "remix", "Remix", "REMIX", "翻自", "原唱", "钢琴", "伴奏",
    "伤感版", "女声", "男声", "Cover", "cover", "Acoustic",
    "Live版", "live版", "Live)", "Live）", "正式版",
    " beat", " Beat", " BEAT", "Type Beat",
)
_LYRIC_SCORE_THRESHOLD = 25
_FAKE_ARTIST_SUFFIX_CHARS = "-.·、_~—,、."


def _strip_paren(s: str) -> str:
    """去掉歌名尾部的括号后缀"""
    s = (s or "").strip().lower()
    for ch in ("(", "（"):
        i = s.find(ch)
        if i > 0:
            s = s[:i].strip()
    return s


def _is_fake_artist_variant(candidate: str, target: str) -> bool:
    if not candidate or not target:
        return False
    stripped = candidate.rstrip(_FAKE_ARTIST_SUFFIX_CHARS).strip()
    return stripped == target and stripped != candidate


def _has_extra_junk(candidate: str, target: str) -> bool:
    for kw in _LYRIC_JUNK_KW:
        if kw in candidate and kw not in target:
            return True
    return False


def _score_candidates(songs: list, title: str, artist: str) -> list:
    """对候选歌曲列表打分，返回 [(score, song_id, duration_sec), ...] 按分数降序。"""
    target_title = _strip_paren(title)
    target_artist = (artist or "").strip().lower()

    candidates = []
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
    return candidates


# ============================================================
# Netease Music (Public API, no third-party service)
# ============================================================


def _search_netease(title: str, artist: str) -> list:
    """搜索网易云公开接口，返回统一候选列表。"""
    kw = f"{title} {artist}"
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
            for s in songs if s.get("id")
        ]
    except Exception as e:
        logger.error(f"[media] netease search error: {e}")
        return []


def _fetch_netease_lyrics(song_id: int) -> str:
    """从网易云公开接口获取 LRC 歌词文本。"""
    try:
        resp = _requests.get(
            "http://music.163.com/api/song/lyric",
            params={"id": song_id, "lv": -1, "kv": -1, "tv": -1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        return (data.get("lrc") or {}).get("lyric", "") or ""
    except Exception as e:
        logger.error(f"[media] netease lyric error: {e}")
        return ""


# ============================================================
# QQ Music (Public API)
# ============================================================


def _search_qq_music(title: str, artist: str) -> list:
    """搜索 QQ 音乐公开接口，返回统一候选列表。"""
    kw = f"{title} {artist}"
    try:
        resp = _requests.get(
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
            params={"w": kw, "format": "json", "p": 1, "n": 20, "cr": 1},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://y.qq.com"},
            timeout=5,
        )
        data = resp.json()
        songs = (data.get("data") or {}).get("song") or {}
        song_list = songs.get("list") or []
        return [
            {
                "id": s.get("songmid", ""),
                "name": s.get("songname", "") or "",
                "artists": [(singer.get("name", "") or "") for singer in (s.get("singer") or [])],
                "duration_sec": s.get("interval") or 0,
            }
            for s in song_list if s.get("songmid")
        ]
    except Exception as e:
        logger.error(f"[media] qq music search error: {e}")
        return []


def _fetch_qq_lyrics(songmid: str) -> str:
    """从 QQ 音乐公开接口获取 LRC 歌词文本（base64 编码）。"""
    try:
        resp = _requests.get(
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
            params={"songmid": songmid, "format": "json", "nobase64": 0},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://y.qq.com",
            },
            timeout=5,
        )
        data = resp.json()
        lyric_b64 = data.get("lyric", "")
        if lyric_b64:
            return base64.b64decode(lyric_b64).decode("utf-8", errors="replace")
        return ""
    except Exception as e:
        logger.error(f"[media] qq music lyric error: {e}")
        return ""


# ============================================================
# Lyrics Search Orchestration (Netease → QQ Music fallback)
# ============================================================

_lyrics_cache = {}
_lyrics_cache_lock = threading.Lock()
_LYRICS_CACHE_MAX = 20
_lyrics_cache_order = []


def _load_lyrics_by_netease_id(song_id: int, duration_sec: float) -> dict:
    """按网易云 song_id 拉取并解析歌词。"""
    lrc_text = _fetch_netease_lyrics(song_id)
    lyrics = _parse_lrc(lrc_text) if lrc_text else []
    return {"song_id": song_id, "duration": duration_sec, "lyrics": lyrics, "manual": False}


def _search_and_fetch(title: str, artist: str) -> dict | None:
    """双源搜索：网易云优先，QQ 音乐 fallback。返回结果 dict 或 None。"""

    # 1. 网易云搜索
    netease_songs = _search_netease(title, artist)
    candidates = _score_candidates(netease_songs, title, artist)
    for score, sid, dur in candidates[:3]:
        r = _load_lyrics_by_netease_id(sid, dur)
        if r["lyrics"]:
            return r

    # 2. QQ 音乐 fallback
    qq_songs = _search_qq_music(title, artist)
    qq_candidates = _score_candidates(qq_songs, title, artist)
    for score, songmid, dur in qq_candidates[:3]:
        lrc_text = _fetch_qq_lyrics(songmid)
        lyrics = _parse_lrc(lrc_text) if lrc_text else []
        if lyrics:
            return {"song_id": songmid, "duration": dur, "lyrics": lyrics, "manual": False}

    if not candidates and not qq_candidates:
        logger.warning(f"[media] no match on any source: {title} - {artist}")
    return None


def _get_lyrics_for(title: str, artist: str, song_id: int = 0) -> dict:
    """获取歌词（带缓存）。song_id 可从 YesPlayMusic API 直接提供。"""
    key = (title, artist)

    with _lyrics_cache_lock:
        cached = _lyrics_cache.get(key)
    if cached is not None:
        return cached

    # Check persisted song_id override
    if not song_id:
        overrides = _load_song_id_overrides()
        override_key = f"{title}|||{artist}"
        if override_key in overrides:
            song_id = int(overrides[override_key].get("song_id", 0))

    result = None

    if song_id:
        r = _load_lyrics_by_netease_id(song_id, 0.0)
        if r["lyrics"]:
            result = r

    if not result:
        result = _search_and_fetch(title, artist)

    if not result:
        return {"song_id": None, "duration": 0.0, "lyrics": [], "manual": False}

    if result["lyrics"]:
        with _lyrics_cache_lock:
            _lyrics_cache[key] = result
            _lyrics_cache_order.append(key)
            while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
                old_key = _lyrics_cache_order.pop(0)
                _lyrics_cache.pop(old_key, None)
        logger.info(f"[media] loaded lyrics for: {title} "
                    f"(lrc={len(result['lyrics'])} lines, duration={result['duration']:.1f}s)")

    return result


# ============================================================
# Public API
# ============================================================


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

    lyric_data = _get_lyrics_for(info["title"], info["artist"],
                                  song_id=info.get("song_id"))
    lyrics = lyric_data["lyrics"]
    duration = lyric_data["duration"]
    song_id = lyric_data.get("song_id")

    ratio = info.get("progress_ratio")
    position_source = "none"
    pos = 0.0

    if info.get("position") and info.get("duration"):
        pos = float(info["position"])
        duration = float(info["duration"])
        position_source = "api"
    elif ratio is not None and duration > 0:
        pos = ratio * duration
        position_source = "uia"

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
        "lyrics_yrc": [],  # YRC removed, keep field for frontend compat
        "song_id": song_id,
        "position": round(pos, 2),
        "duration": round(duration, 2),
        "progress_ratio": ratio,
        "position_source": position_source,
    }


# ============================================================
# Lyric Offset
# ============================================================

_LYRIC_OFFSET_FILE = CONFIG_DIR / "lyric_offset.json"
_LYRIC_OFFSET_DEFAULT = 1.5


def _load_lyric_offset() -> float:
    try:
        data = json.loads(_LYRIC_OFFSET_FILE.read_text(encoding="utf-8"))
        return float(data.get("offset", _LYRIC_OFFSET_DEFAULT))
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return _LYRIC_OFFSET_DEFAULT


def _save_lyric_offset(val: float):
    try:
        _LYRIC_OFFSET_FILE.write_text(json.dumps({"offset": val}), encoding="utf-8")
    except OSError:
        pass


# ============================================================
# Service-Level Functions (called by dashboard routes)
# ============================================================


def reload_current_media() -> dict:
    """Clear the current song lyric cache and return fresh media info."""
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
        logger.info(f"[media] lyrics cache cleared for: {title}")
    return get_media_info()


def set_current_song_id(song_id_value) -> tuple[dict, int]:
    """Manually bind the active song to a song_id and return (payload, status)."""
    try:
        song_id = int(song_id_value)
    except (TypeError, ValueError):
        return {"error": "invalid song_id"}, 400

    with _smtc_lock:
        title = _smtc_result.get("title", "")
        artist = _smtc_result.get("artist", "")
    if not title:
        return {"error": "no active song"}, 400

    result = _load_lyrics_by_netease_id(song_id, 0.0)
    result["manual"] = True

    key = (title, artist)
    with _lyrics_cache_lock:
        _lyrics_cache[key] = result
        if key not in _lyrics_cache_order:
            _lyrics_cache_order.append(key)
        while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
            old_key = _lyrics_cache_order.pop(0)
            _lyrics_cache.pop(old_key, None)
    _save_song_id_override(title, artist, song_id)
    logger.info(f"[media] manually set song_id={song_id} for: {title}")
    return get_media_info(), 200


def get_media_status() -> dict:
    """Return SMTC worker status without starting or contacting the worker."""
    with _smtc_lock:
        last_update = _smtc_last_update
        media_state = _smtc_result.get("status", "idle")
    age = time.time() - last_update if last_update else None
    stale = bool(_smtc_started and age is not None and age > 15)
    if not _smtc_started:
        status = "unknown"
    elif stale:
        status = "stale"
    elif last_update:
        status = "ok"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "stale": stale,
        "error": None,
        "last_success_at": last_update or None,
        "details": {"media_state": media_state, "worker_started": _smtc_started},
    }


def load_lyric_offset() -> float:
    return _load_lyric_offset()


def save_lyric_offset(val: float):
    _save_lyric_offset(val)


def normalize_lyric_offset(payload: dict) -> float:
    if "delta" in payload:
        return round((_load_lyric_offset() + float(payload["delta"])) * 10) / 10
    return round(float(payload.get("offset", _LYRIC_OFFSET_DEFAULT)) * 10) / 10
