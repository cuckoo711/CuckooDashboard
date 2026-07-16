"""Settings 服务兼容导出；实现位于 ``features.settings``。"""

from features.settings.persistence import reveal_secret
from features.settings.runtime import apply_runtime_config
from features.settings.schema import SECRET_MASK, SettingsValidationError
from features.settings.service import (
    get_settings_options,
    get_settings_payload,
    save_settings_payload,
)

__all__ = [
    "SECRET_MASK",
    "SettingsValidationError",
    "apply_runtime_config",
    "get_settings_options",
    "get_settings_payload",
    "reveal_secret",
    "save_settings_payload",
]
