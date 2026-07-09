#!/usr/bin/env python3
"""
独立的 SMTC + UI Automation 常驻监听脚本（避免主进程 COM 冲突）
持续运行，每隔 0.5 秒向 stdout 输出一行 JSON，格式：
{"status": "playing", "title": "...", "artist": "...", "progress_ratio": 0.52, "smtc_position": 0}

说明：
- title / artist / status 来自 SMTC（GlobalSystemMediaTransportControlsSessionManager），可靠。
- 网易云音乐客户端不会向 SMTC 写入 timeline（position 永远为 0），这是网易云自身的实现限制，
  已通过 winrt 和原生 PowerShell WinRT 调用双重验证确认，不是本脚本的读取问题。
- 因此播放进度改用 UI Automation 读取网易云播放器窗口里的进度条控件，
  取"已播放宽度 / 总轨道宽度"的比例（progress_ratio），比 SMTC 更可靠。
  后端结合网易云 API 查到的歌曲总时长，换算出真实的当前播放秒数。
"""
import asyncio
import json
import sys
import time

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

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


def get_progress_ratio():
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
        if len(children) < 2:
            return None

        # 注意：slider 自身的宽度才是稳定的总轨道宽度（1022px 量级，不随播放变化）。
        # children[0] 是背景轨道容器（宽度也稳定，但更保险用 slider 自身）。
        # children[1] 才是"已播放进度"条，宽度随播放进度平滑增长。
        # 之前误把 children[1]/children[2] 当作 track/played，
        # 而 children[2] 实际是固定 4px 的拖动手柄圆点，不代表进度——这是导致播放进度读数
        # 时而正常时而跳变的根本原因（已通过连续采样验证修正）。
        total_rect = slider.BoundingRectangle
        played_rect = children[1].BoundingRectangle
        total_w = total_rect.right - total_rect.left
        played_w = played_rect.right - played_rect.left
        if total_w <= 0:
            return None
        ratio = played_w / total_w
        return max(0.0, min(1.0, ratio))
    except Exception:
        return None


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


async def main_loop():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )

    manager = await MediaManager.request_async()
    while True:
        try:
            info = await get_smtc_info(manager)
            info["progress_ratio"] = get_progress_ratio()
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
