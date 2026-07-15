#!/usr/bin/env python3
"""
独立的 SMTC + UI Automation 常驻监听脚本（避免主进程 COM 冲突）
持续运行，每隔 0.5 秒向 stdout 输出一行 JSON。

数据来源优先级：
1. YesPlayMusic 本地 API（http://127.0.0.1:27232/player）— position + duration + cover URL
2. SMTC title/artist/status + UIA progress_ratio（fallback）

封面策略：
- 优先 YesPlayMusic / 网易云 picUrl（轻量 HTTP URL，不塞 base64）
- SMTC thumbnail 异步写出到 data/media_cover.bin，只通知主进程刷新
"""
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

# ============================================================
# YesPlayMusic 本地 API
# ============================================================

_YPM_API = "http://127.0.0.1:27232/player"
_ym_cache = {"ts": 0, "ok": False}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_COVER_FILE = _PROJECT_ROOT / "data" / "media_cover.bin"
_COVER_META = _PROJECT_ROOT / "data" / "media_cover.json"


def _get_ypm_progress() -> dict | None:
    """查询 YesPlayMusic 本地 API。"""
    now = time.time()
    if not _ym_cache["ok"] and (now - _ym_cache["ts"]) < 5:
        return None

    try:
        req = urllib.request.Request(_YPM_API, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _ym_cache["ok"] = True
        _ym_cache["ts"] = now

        track = data.get("currentTrack") or {}
        progress = data.get("progress")  # 秒
        duration_ms = track.get("dt")    # 毫秒

        if progress is None or not duration_ms:
            return None

        artists = " / ".join(a.get("name", "") for a in (track.get("ar") or []))
        album = ""
        cover_url = ""
        al = track.get("al") or {}
        if isinstance(al, dict):
            album = al.get("name") or ""
            cover_url = al.get("picUrl") or ""

        return {
            "position": float(progress),
            "duration": duration_ms / 1000.0,
            "title": track.get("name", ""),
            "artist": artists,
            "album": album,
            "song_id": track.get("id"),
            "cover_url": cover_url,
        }
    except Exception:
        _ym_cache["ok"] = False
        _ym_cache["ts"] = now
        return None


# ============================================================
# UIA 进度条读取（fallback）
# ============================================================

_uia_cache = {"window": None, "ts": 0}


def _find_netease_window():
    now = time.time()
    if _uia_cache["window"] is not None and (now - _uia_cache["ts"]) < 3:
        return _uia_cache["window"]
    win = None
    try:
        for w in auto.GetRootControl().GetChildren():
            if w.ClassName == "OrpheusBrowserHost":
                win = w
                break
    except Exception:
        win = None
    _uia_cache["window"] = win
    _uia_cache["ts"] = now
    return win


def _find_by_name(ctrl, name, depth=0, max_depth=15):
    if ctrl.Name == name:
        return ctrl
    if depth > max_depth:
        return None
    try:
        for child in ctrl.GetChildren():
            r = _find_by_name(child, name, depth + 1, max_depth)
            if r:
                return r
    except Exception:
        pass
    return None


def _get_uia_progress_ratio():
    if not HAS_UIA:
        return None
    try:
        win = _find_netease_window()
        if not win:
            return None
        try:
            wp = win.GetWindowPattern()
            if wp and wp.WindowVisualState == 2:  # Minimized
                return None
        except Exception:
            pass

        slider = _find_by_name(win, "播放进度调节")
        if not slider:
            return None
        children = slider.GetChildren()
        if not children:
            return None

        total_rect = slider.BoundingRectangle
        total_w = total_rect.right - total_rect.left
        if total_w <= 0:
            return None

        played_w = 0
        for c in children:
            try:
                r = c.BoundingRectangle
                w = r.right - r.left
                if 6 < w < total_w and w > played_w:
                    played_w = w
            except Exception:
                continue

        if played_w <= 0:
            return 0.0
        ratio = played_w / total_w
        return max(0.0, min(1.0, ratio))
    except Exception:
        return None


# ============================================================
# SMTC + async cover extract
# ============================================================

_cover_cache = {"key": "", "mime": "", "version": 0}
_cover_task: asyncio.Task | None = None


async def _save_thumbnail(thumbnail, key: str) -> None:
    """Read SMTC thumbnail and dump to data/media_cover.bin (non-blocking)."""
    if not thumbnail:
        return
    try:
        from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions

        stream = await asyncio.wait_for(thumbnail.open_read_async(), timeout=1.5)
        size = int(stream.size or 0)
        if size <= 0 or size > 2_000_000:
            return
        buf = Buffer(size)
        await asyncio.wait_for(stream.read_async(buf, size, InputStreamOptions.NONE), timeout=1.5)
        reader = DataReader.from_buffer(buf)
        data = bytearray(size)
        reader.read_bytes(data)
        raw = bytes(data)
        mime = "image/jpeg"
        if raw.startswith(b"\x89PNG"):
            mime = "image/png"
        elif raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            mime = "image/webp"
        elif raw.startswith(b"GIF8"):
            mime = "image/gif"

        _COVER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COVER_FILE.write_bytes(raw)
        meta = {
            "key": key,
            "mime": mime,
            "updated_at": time.time(),
            "size": len(raw),
        }
        _COVER_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _cover_cache["key"] = key
        _cover_cache["mime"] = mime
        _cover_cache["version"] = int(time.time() * 1000)
    except Exception:
        return


async def get_smtc_info(manager):
    global _cover_task
    session = manager.get_current_session()
    if not session:
        return {"status": "idle", "title": "", "artist": "", "album": ""}

    props = await session.try_get_media_properties_async()
    playback = session.get_playback_info()

    status_map = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
    status = status_map.get(playback.playback_status, "unknown")
    title = props.title or ""
    artist = props.artist or ""
    album = getattr(props, "album_title", None) or ""

    info = {
        "status": status,
        "title": title,
        "artist": artist,
        "album": album or "",
    }

    key = f"{title}\n{artist}\n{album}"
    if title and key != _cover_cache["key"]:
        info["cover_pending"] = True
        if _cover_task is None or _cover_task.done():
            thumb = props.thumbnail
            _cover_task = asyncio.create_task(_save_thumbnail(thumb, key))
    elif title and _cover_cache["version"]:
        info["cover_file"] = True
        info["cover_version"] = _cover_cache["version"]
        info["cover_mime"] = _cover_cache["mime"]

    return info


async def main_loop():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )

    manager = await MediaManager.request_async()
    last_cover_version = 0
    while True:
        try:
            smtc_info = await get_smtc_info(manager)
            ypm = _get_ypm_progress()

            if ypm:
                info = {
                    "status": smtc_info.get("status") or "playing",
                    "title": ypm["title"] or smtc_info.get("title", ""),
                    "artist": ypm["artist"] or smtc_info.get("artist", ""),
                    "album": ypm.get("album") or smtc_info.get("album") or "",
                    "progress_ratio": (
                        ypm["position"] / ypm["duration"] if ypm["duration"] > 0 else None
                    ),
                    "position": ypm["position"],
                    "duration": ypm["duration"],
                    "song_id": ypm.get("song_id"),
                }
                if ypm.get("cover_url"):
                    info["cover_url"] = ypm["cover_url"]
                for k in ("cover_file", "cover_version", "cover_mime", "cover_pending"):
                    if k in smtc_info and k not in info:
                        info[k] = smtc_info[k]
            else:
                info = smtc_info
                ratio = _get_uia_progress_ratio()
                info["progress_ratio"] = ratio

            ver = int(_cover_cache.get("version") or 0)
            if ver and ver != last_cover_version:
                last_cover_version = ver
                info["cover_file"] = True
                info["cover_version"] = ver
                info["cover_mime"] = _cover_cache.get("mime") or "image/jpeg"
                info["cover_changed"] = True

        except Exception as e:
            info = {
                "status": "error",
                "title": "",
                "artist": "",
                "progress_ratio": None,
                "error": str(e),
            }

        line = json.dumps(info, ensure_ascii=False)
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
