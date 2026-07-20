"""WASAPI loopback spectrum analysis for the /music stage.

Optional dependency stack:
  - sounddevice (WASAPI loopback capture)
  - numpy (FFT)

When deps or devices are unavailable the service reports ``available=False``
and returns a quiet spectrum frame so the UI can degrade gracefully.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any


from core.config import load_config, set_config_value

logger = logging.getLogger("cuckoo.spectrum")

_BINS_DEFAULT = 96
_SAMPLE_RATE = 48000
_BLOCKSIZE = 2048
_CHANNELS = 2

_OFFSET_MS_MIN = -200
_OFFSET_MS_MAX = 200
_SPECTRUM_OFFSET_DEFAULT = 40
_ORBIT_YAW_MAX = 45.0
_ORBIT_PITCH_MAX = 30.0
_ORBIT_PITCH_DEFAULT = 14.0

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception as exc:  # pragma: no cover
    np = None  # type: ignore
    _HAS_NUMPY = False
    logger.info("[spectrum] numpy unavailable: %s", exc)


def _install_ole32_finder() -> None:
    """Make soundcard importable when ctypes cannot resolve short system DLL names.

    ``soundcard`` uses CFFI ``dlopen('ole32')``. On some Windows Python builds,
    ``ctypes.util.find_library('ole32')`` returns ``None`` while the same loader
    succeeds with the full system module name ``ole32.dll``. Prefer the portable
    system name instead of a hardcoded filesystem path, and never ship or load a
    bundled system DLL from the project tree.
    """
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return

    original = getattr(ctypes.util, "find_library", None)
    if original is None:
        return
    if getattr(original, "__cuckoo_ole32_patched__", False):
        return

    def find_library(name: str):  # type: ignore[no-untyped-def]
        key = str(name or "").strip().lower()
        if key in {"ole32", "ole32.dll"}:
            resolved = original("ole32.dll") or original("ole32")
            if resolved:
                return resolved
            # CFFI/Windows can load system modules by basename even when the
            # FindLibrary helper is incomplete. Returning the module name is
            # portable and avoids absolute System32 hardcoding.
            return "ole32.dll"
        return original(name)

    find_library.__cuckoo_ole32_patched__ = True  # type: ignore[attr-defined]
    ctypes.util.find_library = find_library  # type: ignore[assignment]


try:
    _install_ole32_finder()
    import soundcard as sc
    _HAS_SOUNDCARD = True
except Exception as exc:
    sc = None  # type: ignore
    _HAS_SOUNDCARD = False
    logger.info("[spectrum] soundcard unavailable: %s", exc)

try:
    import sounddevice as sd
    _HAS_SOUNDDEVICE = True
except Exception:
    sd = None  # type: ignore
    _HAS_SOUNDDEVICE = False

_HAS_AUDIO = _HAS_NUMPY and (_HAS_SOUNDCARD or _HAS_SOUNDDEVICE)


_lock = threading.Lock()
_lifecycle_lock = threading.RLock()
_state = {
    "bins": [0.0] * _BINS_DEFAULT,
    "rms": 0.0,
    "bass": 0.0,
    "mid": 0.0,
    "high": 0.0,
    "onset": 0.0,
    "ts": 0.0,
    "available": False,
    "enabled": True,
    "error": None,
    "device": "",
}
_ref_count = 0
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_started = False
# Keep the native WASAPI recorder open briefly while clients reconnect.  This
# avoids close/open churn on the player device when a LAN WebSocket flaps.
_CAPTURE_LINGER_S = 5.0
_idle_until = 0.0


def _capture_should_continue() -> bool:
    """Return whether an active subscriber or handoff window still needs capture."""
    with _lock:
        return _ref_count > 0 or time.monotonic() < _idle_until


_prev_flux = 0.0
_prev_mags: Any = None
_smooth_bins: Any = None
_peak_hold: Any = None
_noise_floor_rms = 1e-4
_display_gain = 1.0
_device_cache: dict[str, Any] = {"idx": None, "label": "", "channels": 0, "sr": 0, "ts": 0.0}
_DEVICE_CACHE_TTL = 30.0
_restart_token = 0
_restart_lock = threading.Lock()

# Absolute silence / noise gates on raw PCM RMS.
# True WASAPI loopback music often sits around 0.05~0.5 peak-ish RMS.
_SILENCE_RMS = 0.0010
_ACTIVE_RMS = 0.0025
_NOISE_LEARN_MAX = 0.0008
_LOUD_RMS_REF = 0.35          # raw_rms around here => UI near full body
_DB_FLOOR = -78.0             # spectral bins below this disappear
_DB_CEIL = -18.0              # keep headroom so shape is not glued to 1.0
_RENDER_FPS_MIN = 12
_RENDER_FPS_MAX = 60
_RENDER_BARS_MIN = 12
_RENDER_BARS_MAX = 96



def _clamp_offset_ms(value: float) -> int:
    try:
        val = int(round(float(value)))
    except (TypeError, ValueError):
        val = _SPECTRUM_OFFSET_DEFAULT
    return max(_OFFSET_MS_MIN, min(_OFFSET_MS_MAX, val))


def _clamp_render_fps(value: Any) -> int:
    """0 means automatic client profile; otherwise keep a sensible display rate."""
    try:
        val = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if val <= 0:
        return 0
    return max(_RENDER_FPS_MIN, min(_RENDER_FPS_MAX, val))


def _clamp_render_bars(value: Any) -> int:
    """0 means automatic client profile; otherwise choose a display-bar budget."""
    try:
        val = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if val <= 0:
        return 0
    return max(_RENDER_BARS_MIN, min(_RENDER_BARS_MAX, val))


def _clamp_orbit_yaw(value) -> float:
    try:
        yaw = float(value)
    except (TypeError, ValueError):
        yaw = 0.0
    return max(-_ORBIT_YAW_MAX, min(_ORBIT_YAW_MAX, round(yaw, 2)))


def _clamp_orbit_pitch(value) -> float:
    try:
        pitch = float(value)
    except (TypeError, ValueError):
        pitch = _ORBIT_PITCH_DEFAULT
    return max(-_ORBIT_PITCH_MAX, min(_ORBIT_PITCH_MAX, round(pitch, 2)))


def load_music_offsets() -> dict:
    cfg = load_config().get("music") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    device = str(cfg.get("capture_device") or "auto").strip() or "auto"
    return {
        "spectrum_offset_ms": _clamp_offset_ms(cfg.get("spectrum_offset_ms", _SPECTRUM_OFFSET_DEFAULT)),
        "spectrum_enabled": bool(cfg.get("spectrum_enabled", True)),
        "bins": int(cfg.get("bins", _BINS_DEFAULT) or _BINS_DEFAULT),
        # Rendering options are consumed by the browser. 0 keeps the automatic
        # low-power profile, while manual values are sent with the subscription.
        "render_fps": _clamp_render_fps(cfg.get("render_fps", 0)),
        "render_bars": _clamp_render_bars(cfg.get("render_bars", 0)),
        "capture_device": device,
        "orbit_yaw": _clamp_orbit_yaw(cfg.get("orbit_yaw", 0)),
        "orbit_pitch": _clamp_orbit_pitch(cfg.get("orbit_pitch", _ORBIT_PITCH_DEFAULT)),
    }


def save_music_offsets(payload: dict) -> dict:
    current = load_music_offsets()
    music_cfg = dict(load_config().get("music") or {}) if isinstance(load_config().get("music"), dict) else {}
    device_changed = False

    if "delta_spectrum_offset_ms" in payload:
        current["spectrum_offset_ms"] = _clamp_offset_ms(
            current["spectrum_offset_ms"] + float(payload["delta_spectrum_offset_ms"])
        )
    elif "spectrum_offset_ms" in payload:
        current["spectrum_offset_ms"] = _clamp_offset_ms(payload["spectrum_offset_ms"])

    if "spectrum_enabled" in payload:
        current["spectrum_enabled"] = bool(payload["spectrum_enabled"])
    if "capture_device" in payload:
        new_device = str(payload.get("capture_device") or "auto").strip() or "auto"
        if new_device != current.get("capture_device"):
            device_changed = True
        current["capture_device"] = new_device
    if "bins" in payload:
        try:
            current["bins"] = max(16, min(96, int(payload.get("bins") or _BINS_DEFAULT)))
        except (TypeError, ValueError):
            current["bins"] = _BINS_DEFAULT
    if "render_fps" in payload:
        current["render_fps"] = _clamp_render_fps(payload.get("render_fps"))
    if "render_bars" in payload:
        current["render_bars"] = _clamp_render_bars(payload.get("render_bars"))
    if "orbit_yaw" in payload:
        current["orbit_yaw"] = _clamp_orbit_yaw(payload.get("orbit_yaw"))
    if "orbit_pitch" in payload:
        current["orbit_pitch"] = _clamp_orbit_pitch(payload.get("orbit_pitch"))

    music_cfg.update(current)
    set_config_value("music", music_cfg)
    if device_changed:
        request_capture_restart("capture device changed")
    return current



def _quiet_frame(error: str | None = None, available: bool = False) -> dict:
    bins = int(load_music_offsets().get("bins") or _BINS_DEFAULT)
    bins = max(16, min(96, bins))
    return {
        "ok": available,
        "available": available,
        "enabled": load_music_offsets().get("spectrum_enabled", True),
        "bins": [0.0] * bins,
        "rms": 0.0,
        "bass": 0.0,
        "mid": 0.0,
        "high": 0.0,
        "onset": 0.0,
        "ts": time.time(),
        "error": error,
        "device": "",
        "offsets": load_music_offsets(),
    }


def _hostapi_name(hostapi_index: int) -> str:
    if not _HAS_SOUNDDEVICE:
        return ""
    try:
        return str(sd.query_hostapis(hostapi_index).get("name") or "")
    except Exception:
        return ""


def request_capture_restart(reason: str = "") -> None:
    """Ask the capture loop to reopen (e.g. after capture_device change)."""
    global _restart_token
    with _restart_lock:
        _restart_token += 1
        token = _restart_token
    _device_cache.update({"idx": None, "label": "", "ts": 0.0})
    logger.info("[spectrum] capture restart requested (#%s) %s", token, reason or "")


def _current_restart_token() -> int:
    with _restart_lock:
        return _restart_token


def _ensure_soundcard():
    """Lazy-import soundcard so a long-lived process can pick it up after pip install."""
    global sc, _HAS_SOUNDCARD
    if _HAS_SOUNDCARD and sc is not None:
        return True
    try:
        _install_ole32_finder()
        import soundcard as _sc
        sc = _sc
        _HAS_SOUNDCARD = True
        return True
    except Exception as exc:
        logger.info("[spectrum] soundcard still unavailable: %s", exc)
        _HAS_SOUNDCARD = False
        sc = None
        return False


class _ComApartment:
    """Initialize COM for the current thread (required by soundcard/WASAPI).

    Flask/Werkzeug serves requests on worker threads without COM. soundcard then
    fails with 0x800401f0 (CO_E_NOTINITIALIZED), which made the device list empty.
    """

    def __init__(self):
        self._mode = None  # "pywin32" | "comtypes" | None

    def __enter__(self):
        # Prefer pywin32; fall back to comtypes.
        try:
            import pythoncom
            pythoncom.CoInitialize()
            self._mode = "pywin32"
            return self
        except Exception:
            pass
        try:
            from comtypes import COINIT_MULTITHREADED, CoInitializeEx
            CoInitializeEx(COINIT_MULTITHREADED)
            self._mode = "comtypes"
            return self
        except Exception as exc:
            logger.debug("[spectrum] COM init skipped: %s", exc)
            self._mode = None
            return self

    def __exit__(self, exc_type, exc, tb):
        if self._mode == "pywin32":
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
        elif self._mode == "comtypes":
            try:
                from comtypes import CoUninitialize
                CoUninitialize()
            except Exception:
                pass
        return False


def _enum_soundcard_loopbacks() -> list[dict[str, Any]]:
    """Best-effort enumeration of WASAPI loopback endpoints via soundcard."""
    if not _ensure_soundcard():
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(mic, default_name: str = "") -> None:
        try:
            is_loop = bool(getattr(mic, "isloopback", False))
            if not is_loop:
                return
            name = str(getattr(mic, "name", "") or "unknown")
            mid = str(getattr(mic, "id", "") or name)
            key = mid or name
            if key in seen:
                return
            seen.add(key)
            is_default = bool(default_name and (default_name in name or name in default_name))
            is_virtual = any(k in name.lower() for k in ("wdm2vst", "cable", "vb-audio", "virtual"))
            items.append({
                "id": f"sc:{mid}",
                "label": f"{'★ ' if is_default else ''}Loopback · {name}",
                "kind": "loopback",
                "backend": "soundcard",
                "name": name,
                "recommended": is_default,
                "group": "loopback",
                "virtual": is_virtual,
            })
        except Exception:
            return

    with _ComApartment():
        try:
            default_spk = None
            default_name = ""
            try:
                default_spk = sc.default_speaker()
                default_name = getattr(default_spk, "name", "") or ""
            except Exception as exc:
                logger.warning("[spectrum] default_speaker failed: %s", exc)

            # Path A: include_loopback listing
            try:
                for mic in sc.all_microphones(include_loopback=True):
                    _add(mic, default_name)
            except Exception as exc:
                logger.warning("[spectrum] all_microphones(include_loopback=True) failed: %s", exc)

            # Path B: speakers -> get_microphone(id/name, include_loopback=True)
            try:
                speakers = list(sc.all_speakers())
            except Exception as exc:
                logger.warning("[spectrum] all_speakers failed: %s", exc)
                speakers = []
            if default_spk is not None:
                speakers = [default_spk] + [
                    s for s in speakers if getattr(s, "id", None) != getattr(default_spk, "id", None)
                ]
            for spk in speakers:
                for getter in (
                    lambda s=spk: sc.get_microphone(id=str(getattr(s, "id", "")), include_loopback=True),
                    lambda s=spk: sc.get_microphone(getattr(s, "name", ""), include_loopback=True),
                ):
                    try:
                        mic = getter()
                        _add(mic, default_name)
                        break
                    except Exception:
                        continue

            items.sort(key=lambda d: (
                0 if d.get("recommended") else 1,
                1 if d.get("virtual") else 0,
                d.get("label") or "",
            ))
            logger.info("[spectrum] enumerated %s loopback device(s)", len(items))
            return items
        except Exception as exc:
            logger.warning("[spectrum] enumerate loopbacks failed: %s", exc)
            return []


def _enum_sounddevice_loopback_inputs() -> list[dict[str, Any]]:
    """Expose capture-capable loopback/mix endpoints through sounddevice.

    sounddevice 0.5.x does not synthesize a WASAPI loopback stream from an
    output endpoint. Instead, PortAudio exposes real Stereo Mix, What-U-Hear,
    and virtual mix endpoints as input devices. Those are directly capturable
    by ``sd.InputStream`` and must be offered even when soundcard cannot load.
    """
    if not _HAS_SOUNDDEVICE:
        return []
    mix_keywords = (
        "立体声混音", "stereo mix", "what u hear", "wave out mix",
        "loopback", "混音", "mix (", "mix)", "wdm2vst",
    )
    try:
        items: list[dict[str, Any]] = []
        for index, device in enumerate(sd.query_devices()):
            max_in = int(device.get("max_input_channels") or 0)
            if max_in <= 0:
                continue
            name = str(device.get("name") or f"device-{index}")
            lower = name.lower()
            if not any(token in lower or token in name for token in mix_keywords):
                continue
            if any(token in lower or token in name for token in ("microphone", "麦克风", "mic ")):
                continue
            host = _hostapi_name(int(device.get("hostapi") or -1))
            host_lower = host.lower()
            virtual = any(token in lower for token in ("wdm2vst", "cable", "vb-audio", "virtual"))
            is_stereo_mix = "stereo mix" in lower or "立体声混音" in name
            items.append({
                "id": f"sd:{index}",
                "label": f"{'★ ' if is_stereo_mix else ''}Loopback · {name} [{host}]",
                "kind": "loopback",
                "backend": "sounddevice",
                "name": name,
                "index": index,
                "recommended": is_stereo_mix,
                "group": "loopback",
                "virtual": virtual,
                "_priority": (
                    0 if is_stereo_mix and "wdm-ks" in host_lower else
                    1 if is_stereo_mix else
                    2 if "wasapi" in host_lower else
                    3 if "directsound" in host_lower else
                    4
                ),
            })
        items.sort(key=lambda item: (
            item["_priority"],
            1 if item.get("virtual") else 0,
            item.get("label") or "",
        ))
        for item in items:
            item.pop("_priority", None)
        return items
    except Exception as exc:
        logger.warning("[spectrum] sounddevice loopback enumeration failed: %s", exc)
        return []


def list_capture_devices(*, include_advanced: bool = False) -> list[dict[str, Any]]:
    """Enumerate selectable capture devices for settings UI.

    Preferred list:
      - auto
      - soundcard WASAPI loopbacks (true system output)

    Advanced (include_advanced=True): mix-like sounddevice endpoints only.
    """
    devices: list[dict[str, Any]] = [{
        "id": "auto",
        "label": "自动（优先默认播放设备的 Loopback）",
        "kind": "auto",
        "backend": "auto",
        "recommended": True,
        "group": "recommended",
    }]

    loopback_items = _enum_soundcard_loopbacks()
    if not loopback_items:
        # soundcard may be installed but unusable in a non-native shell because
        # its Media Foundation binding cannot load ole32. PortAudio still
        # exposes capture-capable Stereo Mix and virtual mix endpoints.
        loopback_items = _enum_sounddevice_loopback_inputs()
    devices.extend(loopback_items)

    if not loopback_items:
        devices.append({
            "id": "__none__",
            "label": "未检测到 Loopback（请点刷新设备，或重启 Dashboard）",
            "kind": "warning",
            "backend": "auto",
            "recommended": False,
            "group": "warning",
            "disabled": True,
        })

    advanced: list[dict[str, Any]] = []
    if _HAS_SOUNDDEVICE and include_advanced:
        try:
            for i, dev in enumerate(sd.query_devices()):
                max_in = int(dev.get("max_input_channels") or 0)
                if max_in <= 0:
                    continue
                host = _hostapi_name(int(dev.get("hostapi") or 0))
                if "wdm-ks" in host.lower():
                    continue
                name = str(dev.get("name") or f"device-{i}")
                low = name.lower()
                if not any(k in low or k in name for k in (
                    "mix", "混音", "loopback", "stereo mix", "what u hear", "cable", "vb-audio", "wdm2vst"
                )):
                    continue
                if any(k in name for k in ("麦克风", "Microphone", "Mic ")) or "mic" in low:
                    continue
                advanced.append({
                    "id": f"sd:{i}",
                    "label": f"备选 · {name} [{host}]",
                    "kind": "sounddevice",
                    "backend": "sounddevice",
                    "name": name,
                    "index": i,
                    "recommended": False,
                    "group": "advanced",
                })
        except Exception as exc:
            logger.warning("[spectrum] list sounddevice devices failed: %s", exc)

    if include_advanced:
        devices.extend(advanced)
    return devices


def _pick_soundcard_mic(device_key: str = "auto"):
    """Resolve configured capture device to a soundcard microphone object.

    Caller must initialize COM on this thread (see ``_ComApartment``).
    """
    if not _ensure_soundcard():
        return None, ""
    try:
        mics = list(sc.all_microphones(include_loopback=True))
        if not mics:
            mics = []
            for spk in sc.all_speakers():
                try:
                    mics.append(sc.get_microphone(id=str(getattr(spk, "id", "")), include_loopback=True))
                except Exception:
                    try:
                        mics.append(sc.get_microphone(getattr(spk, "name", ""), include_loopback=True))
                    except Exception:
                        continue

        key = (device_key or "auto").strip() or "auto"
        if key.startswith("sc:"):
            target = key[3:]
            for m in mics:
                mid = str(getattr(m, "id", "") or "")
                name = str(getattr(m, "name", "") or "")
                if target and (target == mid or target == name or target in mid or target in name):
                    is_loop = bool(getattr(m, "isloopback", False))
                    label = f"{'WASAPI loopback' if is_loop else 'Input'}: {name}"
                    return m, label
            try:
                m = sc.get_microphone(id=target, include_loopback=True)
                return m, f"WASAPI loopback: {getattr(m, 'name', target)}"
            except Exception:
                pass

        if key == "auto" or key.startswith("sc:"):
            spk = sc.default_speaker()
            spk_name = getattr(spk, "name", "") or ""
            loopbacks = [m for m in mics if getattr(m, "isloopback", False)]
            pick = None
            for m in loopbacks:
                name = getattr(m, "name", "") or ""
                if spk_name and (spk_name in name or name in spk_name):
                    pick = m
                    break
            if pick is None:
                for m in loopbacks:
                    name = (getattr(m, "name", "") or "").lower()
                    if "wdm2vst" not in name:
                        pick = m
                        break
            if pick is None and loopbacks:
                pick = loopbacks[0]
            if pick is None and spk is not None:
                try:
                    pick = sc.get_microphone(id=str(getattr(spk, "id", "")), include_loopback=True)
                except Exception:
                    pick = None
            if pick is not None:
                return pick, f"WASAPI loopback: {getattr(pick, 'name', 'default')}"
        return None, ""
    except Exception as exc:
        logger.warning("[spectrum] soundcard pick failed: %s", exc)
        return None, ""


def _pick_soundcard_loopback():
    """Compatibility wrapper: honor configured capture_device."""
    with _ComApartment():
        return _pick_soundcard_mic(load_music_offsets().get("capture_device") or "auto")


def _find_sounddevice_candidates(device_key: str = "auto") -> list[tuple[int, str, int, int]]:
    """sounddevice candidates. Supports explicit ``sd:<index>`` selection."""
    if not _HAS_SOUNDDEVICE:
        return []
    try:
        devices = list(sd.query_devices())
        key = (device_key or "auto").strip() or "auto"
        if key.startswith("sd:"):
            try:
                idx = int(key.split(":", 1)[1])
            except ValueError:
                idx = -1
            if 0 <= idx < len(devices):
                dev = devices[idx]
                max_in = int(dev.get("max_input_channels") or 0)
                if max_in > 0:
                    host = _hostapi_name(int(dev.get("hostapi") or 0))
                    name = str(dev.get("name") or f"device-{idx}")
                    return [(
                        idx,
                        f"{name} [{host}]",
                        min(_CHANNELS, max_in),
                        int(dev.get("default_samplerate") or _SAMPLE_RATE),
                    )]
            return []

        # auto / sc-failed fallback: mix-like endpoints only
        mix_keywords = (
            "立体声混音", "stereo mix", "what u hear", "wave out mix",
            "loopback", "混音", "mix (", "mix)", "wdm2vst",
        )
        mme_mix, ds_mix, wasapi_mix, ks_mix = [], [], [], []
        for i, dev in enumerate(devices):
            max_in = int(dev.get("max_input_channels") or 0)
            if max_in <= 0:
                continue
            name = str(dev.get("name") or "")
            low = name.lower()
            if not any(k in low or k in name for k in mix_keywords):
                continue
            host = _hostapi_name(int(dev.get("hostapi") or 0))
            host_l = host.lower()
            item = (i, f"{name} [{host}]", min(_CHANNELS, max_in), int(dev.get("default_samplerate") or _SAMPLE_RATE))
            if "wdm-ks" in host_l:
                if any(token in low or token in name for token in ("stereo mix", "立体声混音", "what u hear", "wave out mix", "loopback")):
                    ks_mix.append(item)
                continue
            if "mme" in host_l:
                mme_mix.append(item)
            elif "directsound" in host_l:
                ds_mix.append(item)
            elif "wasapi" in host_l:
                wasapi_mix.append(item)
        return ks_mix + wasapi_mix + ds_mix + mme_mix
    except Exception as exc:
        logger.warning("[spectrum] sounddevice enum failed: %s", exc)
        return []


def _capture_with_soundcard(n_bins: int, device_key: str = "auto") -> bool:
    """Capture via true WASAPI loopback (soundcard). Returns True if opened & ran."""
    # Explicit sounddevice selection should not attempt soundcard first.
    if (device_key or "").startswith("sd:"):
        return False
    with _ComApartment():
        return _capture_with_soundcard_in_com(n_bins, device_key)

def _capture_with_soundcard_in_com(n_bins: int, device_key: str = "auto") -> bool:
    mic, label = _pick_soundcard_mic(device_key)
    if mic is None:
        return False

    samplerate = 48000
    channels = 2
    start_token = _current_restart_token()
    logger.info("[spectrum] opening %s (key=%s)", label, device_key)
    try:
        with mic.recorder(samplerate=samplerate, channels=channels, blocksize=_BLOCKSIZE) as rec:
            with _lock:
                _state["available"] = True
                _state["enabled"] = True
                _state["error"] = None
                _state["device"] = label
            _device_cache.update({
                "idx": device_key or "auto",
                "label": label,
                "channels": channels,
                "sr": samplerate,
                "ts": time.time(),
            })

            # drop a couple of startup discontinuity frames
            try:
                rec.record(numframes=_BLOCKSIZE)
            except Exception:
                pass

            while not _stop_event.is_set() and _capture_should_continue():
                if _current_restart_token() != start_token:
                    logger.info("[spectrum] reopen after restart token change")
                    break
                if not load_music_offsets().get("spectrum_enabled", True):
                    break
                # if settings changed to another device, reopen
                if (load_music_offsets().get("capture_device") or "auto") != (device_key or "auto"):
                    break
                try:
                    block = rec.record(numframes=_BLOCKSIZE)
                except Exception as exc:
                    logger.warning("[spectrum] soundcard record error: %s", exc)
                    time.sleep(0.05)
                    continue

                capture_ts = time.time()
                if getattr(block, "ndim", 1) > 1:
                    mono = np.mean(block, axis=1)
                else:
                    mono = np.asarray(block).reshape(-1)
                mono = np.asarray(mono, dtype=np.float64)

                analyzed = _analyze_block(mono, n_bins, sample_rate=float(samplerate))
                frame = {
                    "ok": True,
                    "available": True,
                    "enabled": True,
                    "device": label,
                    "error": None,
                    "ts": capture_ts,
                    "offsets": load_music_offsets(),
                    **analyzed,
                }
                with _lock:
                    _state.update(frame)
        return True
    except Exception as exc:
        logger.warning("[spectrum] soundcard loopback failed: %s", exc)
        with _lock:
            _state.update(_quiet_frame(error=f"soundcard loopback failed: {exc}", available=False))
        return False


def _capture_with_sounddevice(n_bins: int, device_key: str = "auto") -> bool:
    """Capture a sounddevice Stereo Mix, loopback, or virtual mix endpoint."""
    if not _HAS_SOUNDDEVICE:
        return False
    candidates = _find_sounddevice_candidates(device_key)
    if not candidates:
        return False

    start_token = _current_restart_token()
    for device_idx, device_name, channels, samplerate in candidates:
        if _stop_event.is_set() or not _capture_should_continue():
            break
        ring: list[Any] = []
        ring_lock = threading.Lock()

        def _callback(indata, frames, time_info, status, _ring=ring, _rlock=ring_lock):  # noqa: ARG001
            try:
                with _rlock:
                    _ring.append(indata.copy())
                    if len(_ring) > 8:
                        del _ring[:-4]
            except Exception:
                pass

        try:
            stream_options: dict[str, Any] = {
                "samplerate": int(samplerate or _SAMPLE_RATE),
                "blocksize": _BLOCKSIZE,
                "device": device_idx,
                "channels": channels,
                "dtype": "float32",
                "callback": _callback,
            }
            with sd.InputStream(**stream_options):

                capture_label = device_name + " (sounddevice loopback)"
                logger.info("[spectrum] sounddevice opened: %s", capture_label)
                with _lock:
                    _state["available"] = True
                    _state["enabled"] = True
                    _state["error"] = None
                    _state["device"] = capture_label
                local_rate = float(samplerate or _SAMPLE_RATE)
                silent_frames = 0
                device_silent = False
                while not _stop_event.is_set() and _capture_should_continue():
                    offsets_now = load_music_offsets()
                    if not offsets_now.get("spectrum_enabled", True):
                        break
                    # Mirror the soundcard path: honor restart requests and
                    # capture-device changes from Settings while running.
                    if _current_restart_token() != start_token:
                        logger.info("[spectrum] sounddevice reopen after restart token change")
                        break
                    if (offsets_now.get("capture_device") or "auto") != (device_key or "auto"):
                        break
                    block = None
                    with ring_lock:
                        if ring:
                            block = ring.pop(0)
                    if block is None:
                        time.sleep(0.005)
                        continue
                    if block.ndim > 1:
                        mono = np.mean(block, axis=1)
                    else:
                        mono = block.reshape(-1)
                    analyzed = _analyze_block(mono, n_bins, sample_rate=local_rate)
                    # If fallback device is effectively dead, give up quickly
                    if analyzed.get("silent") and float(analyzed.get("raw_rms") or 0) < 1e-4:
                        silent_frames += 1
                        if silent_frames > 20:
                            logger.warning("[spectrum] fallback device silent, switching away: %s", device_name)
                            # Don't leave UI thinking this is a healthy source
                            with _lock:
                                _state.update(_quiet_frame(
                                    error=f"silent capture device: {device_name}",
                                    available=False,
                                ))
                            device_silent = True
                            break
                    else:
                        silent_frames = 0
                    frame = {
                        "ok": True,
                        "available": True,
                        "enabled": True,
                        "device": capture_label,
                        "error": None,
                        "ts": time.time(),
                        "offsets": load_music_offsets(),
                        **analyzed,
                    }
                    with _lock:
                        _state.update(frame)
            if device_silent:
                # This endpoint produces no signal; try the next candidate
                # instead of reporting success and reopening the same device.
                continue
            return True
        except Exception as exc:
            logger.warning("[spectrum] sounddevice failed on %s: %s", device_name, exc)
            continue
    return False


def _capture_loop():
    global _started
    logger.info("[spectrum] capture loop starting")

    while not _stop_event.is_set() and _capture_should_continue():
        offsets = load_music_offsets()
        if not offsets.get("spectrum_enabled", True):
            with _lock:
                _state.update(_quiet_frame(error="disabled", available=False))
                _state["enabled"] = False
            _stop_event.wait(0.5)
            continue

        if not _HAS_AUDIO or not _HAS_NUMPY:
            with _lock:
                _state.update(_quiet_frame(
                    error="audio stack unavailable (need numpy + soundcard/sounddevice)",
                    available=False,
                ))
            _stop_event.wait(1.0)
            continue

        n_bins = max(16, min(96, int(offsets.get("bins") or _BINS_DEFAULT)))
        opened = False
        device_key = str(offsets.get("capture_device") or "auto")

        # Prefer true WASAPI loopback via soundcard (default speaker = DitooMic).
        # Fall back to sounddevice Stereo Mix / virtual Mix endpoints when
        # soundcard cannot open, or when the user explicitly selected sd:<index>.
        if _HAS_SOUNDCARD and not device_key.startswith("sd:"):
            opened = _capture_with_soundcard(n_bins, device_key=device_key)

        if not opened and not _stop_event.is_set() and _capture_should_continue():
            opened = _capture_with_sounddevice(n_bins, device_key=device_key)

        if not opened:
            with _lock:
                _state.update(_quiet_frame(
                    error="no working loopback capture (install soundcard, or enable Stereo Mix)",
                    available=False,
                ))
            _stop_event.wait(1.5)

        if not _capture_should_continue():
            break

    restart_thread = None
    with _lock:
        _state.update(_quiet_frame(error=None, available=False))
        _state["enabled"] = load_music_offsets().get("spectrum_enabled", True)
        _started = False
        # A subscriber can arrive while the old recorder is closing.  Restart in
        # that narrow handoff window instead of leaving refs > 0 without a thread.
        if _ref_count > 0 and not _stop_event.is_set():
            restart_thread = _start_capture_thread_locked()
    if restart_thread is not None:
        restart_thread.start()
    logger.info("[spectrum] capture loop stopped")


def _analyze_block(mono: Any, n_bins: int, sample_rate: float | None = None) -> dict:
    global _prev_flux, _prev_mags, _smooth_bins, _peak_hold
    global _noise_floor_rms, _display_gain

    rate = float(sample_rate or _SAMPLE_RATE)
    if rate <= 0:
        rate = float(_SAMPLE_RATE)

    rms = float(np.sqrt(np.mean(np.square(mono)))) if len(mono) else 0.0

    # Learn noise floor only in true quiet; never chase music as "noise".
    if rms < _NOISE_LEARN_MAX:
        _noise_floor_rms = min(0.01, _noise_floor_rms * 0.98 + rms * 0.02)
    noise = max(1e-6, float(_noise_floor_rms))

    # Hard silence gate: no fake bars when player is muted / no real output.
    # Require signal clearly above learned floor.
    audible = rms >= max(_SILENCE_RMS, noise * 3.5)
    if not audible:
        zeros = np.zeros(n_bins, dtype=np.float64)
        if _smooth_bins is None or len(_smooth_bins) != n_bins:
            _smooth_bins = zeros.copy()
            _peak_hold = zeros.copy()
        else:
            # decay quickly to zero instead of hovering
            _smooth_bins *= 0.72
            _peak_hold *= 0.8
            _smooth_bins[ _smooth_bins < 0.01 ] = 0.0
            _peak_hold[ _peak_hold < 0.01 ] = 0.0
        _prev_flux *= 0.5
        return {
            "bins": [round(float(x), 4) for x in _smooth_bins.tolist()],
            "peaks": [round(float(x), 4) for x in _peak_hold.tolist()],
            "rms": 0.0,
            "bass": 0.0,
            "mid": 0.0,
            "high": 0.0,
            "onset": 0.0,
            "energy": 0.0,
            "raw_rms": round(rms, 6),
            "silent": True,
        }

    # Hann window + properly scaled amplitude spectrum
    n = int(len(mono))
    window = np.hanning(n)
    win_sum = float(np.sum(window)) or float(n)
    spectrum = np.fft.rfft(mono * window)
    # Amplitude spectrum scaled back to PCM units (~0..1)
    mags = (2.0 / win_sum) * np.abs(spectrum)
    if mags.size <= 1:
        return {
            "bins": [0.0] * n_bins,
            "peaks": [0.0] * n_bins,
            "rms": 0.0,
            "bass": 0.0,
            "mid": 0.0,
            "high": 0.0,
            "onset": 0.0,
            "energy": 0.0,
            "raw_rms": round(rms, 6),
            "silent": False,
        }

    # Ignore DC
    mags = mags[1:]
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)[1:]

    f_min, f_max = 50.0, min(11000.0, rate / 2.0 - 1.0)
    edges = np.geomspace(f_min, f_max, n_bins + 1)
    idx = np.searchsorted(freqs, edges)
    raw_bins = np.zeros(n_bins, dtype=np.float64)
    for i in range(n_bins):
        a = int(idx[i])
        b = int(idx[i + 1])
        if b <= a:
            b = min(len(mags), a + 1)
        if a >= len(mags):
            continue
        band = mags[a:b]
        if band.size:
            # RMS of band magnitudes (already amplitude-scaled)
            raw_bins[i] = float(np.sqrt(np.mean(np.square(band))))

    if n_bins >= 3:
        sm = raw_bins.copy()
        sm[1:-1] = raw_bins[1:-1] * 0.70 + raw_bins[:-2] * 0.15 + raw_bins[2:] * 0.15
        raw_bins = sm

    # Absolute dB from PCM-scale amplitude (approx 0..1)
    eps = 1e-9
    db = 20.0 * np.log10(raw_bins + eps)
    if n_bins > 0:
        db = db + np.linspace(2.0, 0.0, n_bins)

    # Typical music band levels after scaling often sit -55..-15 dB
    norm = (db - _DB_FLOOR) / max(1e-6, (_DB_CEIL - _DB_FLOOR))
    norm = np.clip(norm, 0.0, 1.0)
    norm = np.power(norm, 1.1)

    loud = max(0.0, rms - noise * 2.0)
    level = max(0.0, min(1.0, loud / _LOUD_RMS_REF))
    if rms <= _SILENCE_RMS:
        gate = 0.0
    elif rms >= _ACTIVE_RMS * 2.0:
        gate = 1.0
    else:
        gate = (rms - _SILENCE_RMS) / max(1e-6, (_ACTIVE_RMS * 2.0 - _SILENCE_RMS))
        gate = max(0.0, min(1.0, gate))

    # Shape from spectrum, height follows real volume
    norm = norm * (0.18 + 0.82 * (level ** 0.9)) * gate

    if _smooth_bins is None or len(_smooth_bins) != n_bins:
        _smooth_bins = norm.copy()
        _peak_hold = norm.copy()
    else:
        rising = norm > _smooth_bins
        _smooth_bins = np.where(
            rising,
            _smooth_bins * 0.25 + norm * 0.75,
            _smooth_bins * 0.68 + norm * 0.32,
        )
        _peak_hold = np.maximum(_peak_hold * 0.84, _smooth_bins)
        _smooth_bins[_smooth_bins < 0.01] = 0.0
        _peak_hold[_peak_hold < 0.012] = 0.0

    rms_n = max(0.0, min(1.0, level)) * gate
    bass = float(np.mean(_smooth_bins[: max(1, n_bins // 4)]))
    mid = float(np.mean(_smooth_bins[n_bins // 3: (2 * n_bins) // 3]))
    high = float(np.mean(_smooth_bins[(2 * n_bins) // 3:]))
    energy = float(np.mean(_smooth_bins))

    if _prev_mags is None or len(_prev_mags) != len(mags):
        flux = 0.0
    else:
        prev = np.log1p(_prev_mags * 200.0)
        cur = np.log1p(mags * 200.0)
        diff = cur - prev
        w = np.linspace(1.4, 0.6, num=len(diff))
        flux = float(np.mean(np.clip(diff, 0.0, None) * w))
    _prev_mags = mags

    onset = 0.0
    rising_flux = False
    if gate > 0.25 and level > 0.04:
        onset = max(0.0, min(1.0, flux * 2.2))
        rising_flux = flux > _prev_flux * 1.08 and flux > 0.02 and rms > _ACTIVE_RMS
    _prev_flux = flux * 0.65 + _prev_flux * 0.35

    return {
        "bins": [round(float(x), 4) for x in _smooth_bins.tolist()],
        "peaks": [round(float(x), 4) for x in _peak_hold.tolist()],
        "rms": round(rms_n, 4),
        "bass": round(bass, 4),
        "mid": round(mid, 4),
        "high": round(high, 4),
        "onset": round(onset, 4),
        "energy": round(energy, 4),
        "raw_rms": round(rms, 6),
        "silent": False,
    }


def _start_capture_thread_locked() -> threading.Thread:
    """Create one capture worker while `_lock` is held; caller starts it later."""
    global _thread, _started
    _started = True
    _stop_event.clear()
    _thread = threading.Thread(target=_capture_loop, daemon=True, name="spectrum-loopback")
    return _thread


def acquire_spectrum() -> None:
    """Increase music-stage interest and keep one recorder continuously alive."""
    global _ref_count, _idle_until, _started, _thread
    thread_to_start = None
    with _lifecycle_lock:
        with _lock:
            if _started and (_thread is None or not _thread.is_alive()):
                _started = False
                _thread = None
            _ref_count += 1
            # A newly connected LAN dashboard takes ownership of any short handoff
            # window, so the recorder is not closed and reopened for a reconnection.
            _idle_until = 0.0
            if not _started:
                thread_to_start = _start_capture_thread_locked()
            count = _ref_count
        if thread_to_start is not None:
            thread_to_start.start()
    logger.info("[spectrum] acquire (refs=%s, continuous=%s)", count, True)



def release_spectrum() -> None:
    """Release one dashboard while preserving the recorder across reconnects."""
    global _ref_count, _idle_until
    with _lifecycle_lock:
        with _lock:
            _ref_count = max(0, _ref_count - 1)
            count = _ref_count
            if count == 0:
                # Do not tear down WASAPI immediately: remote browsers often close and
                # reopen a WebSocket during Wi-Fi handoff/page refresh.
                _idle_until = time.monotonic() + _CAPTURE_LINGER_S
            else:
                _idle_until = 0.0
            linger_left = max(0.0, _idle_until - time.monotonic())
    logger.info("[spectrum] release (refs=%s, linger=%.1fs)", count, linger_left)



def shutdown_spectrum(timeout: float = 5) -> None:
    """Drop all subscribers and stop the capture thread, allowing later acquire."""
    global _ref_count, _idle_until, _thread, _started
    with _lifecycle_lock:
        with _lock:
            _ref_count = 0
            _idle_until = 0.0
            _stop_event.set()
            thread = _thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        with _lock:
            if _thread is thread and (thread is None or not thread.is_alive()):
                _thread = None
                _started = False
            _ref_count = 0
            _idle_until = 0.0



def get_spectrum_frame() -> dict:
    """Return the latest spectrum frame (does not start capture)."""
    with _lock:
        frame = dict(_state)
    frame["offsets"] = load_music_offsets()
    if not frame.get("bins"):
        return _quiet_frame(error=frame.get("error"), available=bool(frame.get("available")))
    frame["ok"] = bool(frame.get("available"))
    return frame


def get_spectrum_status() -> dict:
    offsets = load_music_offsets()
    with _lock:
        available = bool(_state.get("available"))
        error = _state.get("error")
        device = _state.get("device") or ""
        refs = _ref_count
    return {
        "status": "ok" if available else ("disabled" if not offsets.get("spectrum_enabled", True) else "unknown"),
        "ok": available,
        "enabled": bool(offsets.get("spectrum_enabled", True)),
        "available": available,
        "has_audio_stack": _HAS_AUDIO,
        "error": error,
        "device": device,
        "subscribers": refs,
        "offsets": offsets,
    }
