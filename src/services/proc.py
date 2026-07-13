"""向后兼容 — 所有导入已迁移到 core.proc。"""
from core.proc import *  # noqa: F401, F403
from core.proc import run_ps, popen_hidden, run_command  # noqa: F401
