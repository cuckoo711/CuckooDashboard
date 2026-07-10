"""SMTC media state and Netease lyrics service."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests as _requests

from services.config import load_config

BASE_DIR = Path(__file__).resolve().parent.parent

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
_SMTC_WORKER = str(BASE_DIR / "smtc_worker.py")
_SMTC_PYTHON = str(BASE_DIR / "venv" / "Scripts" / "python.exe")
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


_LYRIC_OFFSET_FILE = BASE_DIR / "lyric_offset.json"
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


def reload_current_media() -> dict:
    """Clear the current song lyric cache, refresh recent-played cache, and return media info."""
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
    return get_media_info()


def set_current_song_id(song_id_value) -> tuple[dict, int]:
    """Manually bind the active song to a Netease song_id and return (payload, status)."""
    try:
        song_id = int(song_id_value)
    except (TypeError, ValueError):
        return {"error": "invalid song_id"}, 400

    with _smtc_lock:
        title = _smtc_result.get("title", "")
        artist = _smtc_result.get("artist", "")
    if not title:
        return {"error": "no active song"}, 400

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
