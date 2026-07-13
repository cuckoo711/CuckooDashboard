"""显示器枚举与检测模块"""
import ctypes
import ctypes.wintypes
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class RECT(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_long), ('top', ctypes.c_long),
        ('right', ctypes.c_long), ('bottom', ctypes.c_long),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.DWORD),
        ('rcMonitor', RECT),
        ('rcWork', RECT),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('szDevice', ctypes.c_wchar * 32),
    ]


def enum_monitors():
    """枚举所有显示器，返回 [{name, left, top, width, height}]"""
    user32 = ctypes.windll.user32
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_void_p,
    )

    monitors = []

    def callback(hmonitor, hdc, lprect, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
            r = mi.rcMonitor
            monitors.append({
                'name': mi.szDevice,
                'left': r.left, 'top': r.top,
                'width': r.right - r.left,
                'height': r.bottom - r.top,
            })
        return 1

    cb = MONITORENUMPROC(callback)
    user32.EnumDisplayMonitors(None, None, cb, 0)
    return monitors


def find_monitor(name):
    """按设备名查找显示器，返回 {name, left, top, width, height} 或 None"""
    for m in enum_monitors():
        if m['name'] == name:
            return m
    return None


def load_target_monitor(config_path=None):
    """从配置文件读取目标显示器名称并查找，返回显示器信息或 None"""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'monitor.json'
    try:
        cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
        target_name = cfg.get('name')
        if not target_name:
            logger.warning("monitor.json 中未配置 name 字段")
            return None
        return find_monitor(target_name)
    except FileNotFoundError:
        logger.warning("monitor.json 不存在: %s", config_path)
        return None
    except Exception as e:
        logger.warning("读取 monitor.json 失败: %s", e)
        return None
