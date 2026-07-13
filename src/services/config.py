"""向后兼容 — 所有导入已迁移到 core.config。"""
from core.config import *  # noqa: F401, F403
from core.config import (  # noqa: F401
    CONFIG_DIR, CONFIG_FILE, DATA_DIR, PROJECT_ROOT, SRC_DIR,
    get_config_section, load_config, save_config, set_config_value,
)
