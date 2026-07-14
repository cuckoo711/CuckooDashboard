"""Unit tests for Windows performance counter parsing and normalization."""

from __future__ import annotations

from core.perfcounters import (
    WindowsPerformanceSampler,
    aggregate_gpu_engine_samples,
    extract_gpu_luid,
    normalize_gpu_memory_rows,
)


def test_extract_gpu_luid_from_counter_path():
    path = (
        r"\\HOST\GPU Engine("
        r"pid_42_luid_0x00000000_0x00014C65_phys_0_eng_0_engtype_3D)"
        r"\Utilization Percentage"
    )
    assert extract_gpu_luid(path) == "00014c65"


def test_extract_gpu_luid_rejects_unrelated_values():
    assert extract_gpu_luid(None) is None
    assert extract_gpu_luid(r"\Processor(_Total)\% Processor Time") is None


def test_aggregate_gpu_engine_samples_keeps_max_per_luid():
    samples = [
        ("pid_1_luid_0x0_0xAAAA_phys_0_engtype_3D", 12.4),
        ("pid_2_luid_0x0_0xAAAA_phys_0_engtype_Compute", "31"),
        ("pid_3_luid_0x0_0xBBBB_phys_0_engtype_3D", -4),
    ]
    assert aggregate_gpu_engine_samples(samples) == {
        "aaaa": 31,
        "bbbb": 0,
    }


def test_aggregate_gpu_engine_samples_ignores_copy_video_and_invalid_values():
    samples = [
        ("pid_1_luid_0x0_0xAAAA_phys_0_engtype_Copy", 90),
        ("pid_1_luid_0x0_0xAAAA_phys_0_engtype_VideoDecode", 80),
        ("pid_1_luid_0x0_0xAAAA_phys_0_session_1", 70),
        ("pid_1_luid_0x0_0xAAAA_phys_0_engtype_3D", "invalid"),
        ("missing_luid", 50),
    ]
    assert aggregate_gpu_engine_samples(samples) == {}


def test_normalize_gpu_memory_rows_uses_luid_and_highest_duplicate_value():
    rows = [
        ("luid_0x00000000_0x00014C65_phys_0", "1024"),
        ("luid_0x00000000_0x00014C65_phys_1", 2048),
        ("luid_0x00000000_0x00017188_phys_0", None),
        ("unrelated", 9999),
    ]
    assert normalize_gpu_memory_rows(rows) == {
        "00014c65": 2048,
        "00017188": 0,
    }


def test_sampler_degrades_cleanly_without_native_modules(monkeypatch):
    import core.perfcounters as module

    monkeypatch.setattr(module, "pythoncom", None)
    monkeypatch.setattr(module, "win32com", None)
    monkeypatch.setattr(module, "win32pdh", None)

    sampler = WindowsPerformanceSampler()
    assert sampler.available is False
    assert sampler.sample_cpu_frequency_mhz() is None
    assert sampler.sample_gpu_metrics() is None
