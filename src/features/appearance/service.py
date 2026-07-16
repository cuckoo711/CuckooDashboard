"""Appearance payload composition shared by HTTP and WebSocket adapters."""

from __future__ import annotations

from core.config import load_config
from services.font_service import font_exists


def get_font_payload() -> dict:
    """Return the active font and sizing payload consumed by both pages."""
    dashboard_cfg = load_config().get("dashboard") or {}
    font_cfg = dashboard_cfg.get("font") or {}
    enabled = bool(font_cfg.get("enabled"))
    filename = str(font_cfg.get("filename") or "")
    active = enabled and filename and font_exists(filename)
    font_size_cfg = dashboard_cfg.get("font_size") or {}
    return {
        "enabled": enabled,
        "filename": filename,
        "url": f"/static/fonts/{filename}" if active else "",
        "active": bool(active),
        "font_size": {
            "title_text": str(font_size_cfg.get("title_text") or "Cuckoo Dashboard"),
            "title": int(font_size_cfg.get("title", 16)),
            "clock": int(font_size_cfg.get("clock", 22)),
            "date": int(font_size_cfg.get("date", 15)),
            "card_head": int(font_size_cfg.get("card_head", 10)),
            "card_foot": int(font_size_cfg.get("card_foot", 10)),
            "card_body": int(font_size_cfg.get("card_body", 10)),
            "offset": int(font_size_cfg.get("offset", 0)),
        },
    }
