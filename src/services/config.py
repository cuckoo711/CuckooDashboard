"""Local private configuration loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# src/ directory (where source code lives)
SRC_DIR = Path(__file__).resolve().parent.parent

# Project root (parent of src/)
PROJECT_ROOT = SRC_DIR.parent

# User-editable config directory
CONFIG_DIR = PROJECT_ROOT / "config"

# Runtime cache directory (auto-generated, user should not edit)
DATA_DIR = PROJECT_ROOT / "data"

CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict[str, Any]:
    """Load local private config; invalid or missing config returns an empty dict."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def get_config_section(name: str, default: Any = None) -> Any:
    """Return a top-level config section without exposing unrelated local secrets."""
    config = load_config()
    value = config.get(name, default)
    return default if value is None else value
