"""Theme metadata and persistence."""

from __future__ import annotations

from core.config import load_config, set_config_value

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
    """Load active theme index from unified config."""
    config = load_config()
    name = config.get("theme")
    if name:
        idx = theme_index_by_name(name)
        if idx is not None:
            return idx
    return 0


def save_theme_index(index: int):
    """Persist active theme to unified config."""
    set_config_value("theme", THEMES[index]["name"])


def next_theme_index() -> int:
    """Return the next theme index in cyclic order."""
    return (load_theme_index() + 1) % len(THEMES)
