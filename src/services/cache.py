"""向后兼容 — 所有导入已迁移到 core.cache。"""
from core.cache import *  # noqa: F401, F403
from core.cache import TTLCache  # noqa: F401
