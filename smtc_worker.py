#!/usr/bin/env python3
"""
独立的 SMTC + UI Automation 常驻监听脚本（避免主进程 COM 冲突）
持续运行，每隔 0.5 秒向 stdout 输出一行 JSON，格式：
{"status": "playing", "title": "...", "artist": "...", "progress_ratio": 0.52}

数据来源优先级：
1. YesPlayMusic 本地 API（http://127.0.0.1:27232/player）— 直接返回 position + duration
2. SMTC title/artist/status + UIA progress_ratio（fallback）
"""
import asyncio
import json
import sys
import time
import urllib.request

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

# ============================================================
# YesPlayMusic 本地 API
# ============================================================

_YPM_API = "http://127.0.0.1:27232/player"
_ym_cache = {"ts": 0, "ok": False}  # 缓存 API 可用性，避免频繁探测不可用的端口


def _get_ypm_progress() -> dict | None:
    """查询 YesPlayMusic 本地 API，返回 {"position": float, "duration": float, "title": str, "artist": str} 或 None"""
    now = time.time()
    # 如果上次探测失败，每 5 秒重试一次（避免每 0.5s 都打一个不可用的端口）
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

        # 提取艺人名（ar 数组拼接）
        artists = " / ".join(a.get("name", "") for a in (track.get("ar") or []))

        return {
            "position": float(progress),
            "duration": duration_ms / 1000.0,
            "title": track.get("name", ""),
            "artist": artists,
            "song_id": track.get("id"),
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
    """查找网易云音乐播放器窗口（缓存 3 秒，避免每次全量遍历顶层窗口）"""
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
    """通过 UI Automation 读取网易云播放进度条的已播放比例（0.0~1.0），失败返回 None"""
    if not HAS_UIA:
        return None
    try:
        win = _find_netease_window()
        if not win:
            return None

        # 窗口最小化时 BoundingRectangle 不可靠，跳过
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
# SMTC（title / artist / status）
# ============================================================


async def get_smtc_info(manager):
    session = manager.get_current_session()
    if not session:
        return {"status": "idle", "title": "", "artist": ""}

    props = await session.try_get_media_properties_async()
    playback = session.get_playback_info()

    status_map = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
    status = status_map.get(playback.playback_status, "unknown")

    return {
        "status": status,
        "title": props.title or "",
        "artist": props.artist or "",
    }


# ============================================================
# 主循环
# ============================================================


async def main_loop():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )

    manager = await MediaManager.request_async()
    while True:
        try:
            # 优先尝试 YesPlayMusic 本地 API
            ypm = _get_ypm_progress()

            if ypm:
                # API 成功：直接用 position + duration 计算 ratio
                info = {
                    "status": "playing",
                    "title": ypm["title"],
                    "artist": ypm["artist"],
                    "progress_ratio": ypm["position"] / ypm["duration"] if ypm["duration"] > 0 else None,
                    "position": ypm["position"],
                    "duration": ypm["duration"],
                    "song_id": ypm.get("song_id"),
                }
            else:
                # fallback: SMTC + UIA
                info = await get_smtc_info(manager)
                ratio = _get_uia_progress_ratio()
                info["progress_ratio"] = ratio
        except Exception as e:
            info = {"status": "error", "title": "", "artist": "", "progress_ratio": None, "error": str(e)}
        line = json.dumps(info, ensure_ascii=False)
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
