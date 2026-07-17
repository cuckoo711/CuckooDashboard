"""Spectrum capture-device enumeration and loopback readiness."""

from __future__ import annotations

from services import spectrum_service as spectrum


def test_spectrum_prefers_soundcard_loopback_when_available():
    devices = spectrum.list_capture_devices(include_advanced=False)
    assert devices
    assert devices[0]["id"] == "auto"
    loopbacks = [item for item in devices if item.get("kind") == "loopback"]
    assert loopbacks, "expected at least one loopback capture candidate"
    if spectrum._HAS_SOUNDCARD:
        assert any(item.get("backend") == "soundcard" for item in loopbacks)
        recommended = next((item for item in loopbacks if item.get("recommended")), None)
        assert recommended is not None
        assert "Loopback" in recommended["label"]


def test_ole32_finder_uses_portable_system_module_name(monkeypatch):
    import ctypes.util

    calls = []

    def fake_find_library(name):
        calls.append(name)
        return None

    monkeypatch.setattr(ctypes.util, "find_library", fake_find_library)
    # Clear previous patch marker by reinstalling on top of the fake finder.
    delattr(ctypes.util.find_library, "__cuckoo_ole32_patched__", None) if hasattr(
        ctypes.util.find_library, "__cuckoo_ole32_patched__"
    ) else None
    spectrum._install_ole32_finder()
    resolved = ctypes.util.find_library("ole32")
    assert resolved == "ole32.dll"
    assert "C:\\Windows\\System32" not in str(resolved)
    # Reinstall should be idempotent.
    spectrum._install_ole32_finder()
    assert ctypes.util.find_library("ole32") == "ole32.dll"
