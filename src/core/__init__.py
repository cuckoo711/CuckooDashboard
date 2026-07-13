"""core — 项目公共基础模块。

提供配置加载、缓存、路径常量、子进程工具等基础设施，
供 providers 和 services 共同引用。
"""

from core.config import (  # noqa: F401
    CONFIG_DIR,
    CONFIG_FILE,
    DATA_DIR,
    PROJECT_ROOT,
    SRC_DIR,
    get_config_section,
    load_config,
    save_config,
    set_config_value,
)
from core.cache import TTLCache  # noqa: F401
