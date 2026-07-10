"""Windows SMTC playback controls."""

from __future__ import annotations

import logging

import asyncio

logger = logging.getLogger("cuckoo.player")

ALLOWED_PLAYER_ACTIONS = {"play", "pause", "next", "prev", "toggle"}


def control_player(action: str) -> dict:
    """通过 Windows SMTC 发送系统级媒体控制指令。"""

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
