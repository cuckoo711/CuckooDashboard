"""Theme metadata and persistence."""

from __future__ import annotations

import json
from pathlib import Path

from services.config import DATA_DIR

THEME_FILE = DATA_DIR / "display_theme.json"

# 每个主题包含 name + 背景配置（bg_type: "image" | "color"）
THEMES = [
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


def theme_response(idx: int) -> dict:
    """Build the standard theme API response."""
    theme = THEMES[idx]
    return {
        "theme": theme["name"],
        "index": idx,
        "themes": [t["name"] for t in THEMES],
        "bg": {k: theme[k] for k in ("bg_type", "bg_image", "bg_color") if k in theme},
    }


def theme_index_by_name(name: str | None) -> int | None:
    """Find a theme index by name."""
    for i, theme in enumerate(THEMES):
        if theme["name"] == name:
            return i
    return None


def load_theme_index() -> int:
    """Load active theme index; compatible with the legacy {\"index\": 0} format."""
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
        if "theme" in data:
            idx = theme_index_by_name(data.get("theme"))
            if idx is not None:
                return idx
        idx = int(data.get("index", 0))
        if 0 <= idx < len(THEMES):
            return idx
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        pass
    return 0


def save_theme_index(index: int):
    """Persist active theme by stable theme name."""
    try:
        THEME_FILE.write_text(
            json.dumps({"theme": THEMES[index]["name"]}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def next_theme_index() -> int:
    """Return the next theme index in cyclic order."""
    return (load_theme_index() + 1) % len(THEMES)
