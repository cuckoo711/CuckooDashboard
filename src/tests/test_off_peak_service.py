"""Tests for off-peak badge configuration parsing."""

from __future__ import annotations

from services.off_peak_service import build_off_peak_badge_config


def test_defaults_preserve_previous_window():
    assert build_off_peak_badge_config({}) == {
        "enabled": True,
        "ranges": [{"start": "00:00", "end": "08:00"}],
    }


def test_disabled_badge_keeps_configured_ranges():
    result = build_off_peak_badge_config({
        "dashboard": {
            "off_peak_badge": {
                "enabled": False,
                "ranges": [{"start": "01:00", "end": "06:00"}],
            },
        },
    })

    assert result == {
        "enabled": False,
        "ranges": [{"start": "01:00", "end": "06:00"}],
    }


def test_multiple_and_cross_midnight_ranges_are_preserved():
    result = build_off_peak_badge_config({
        "dashboard": {
            "off_peak_badge": {
                "ranges": [
                    {"start": "00:00", "end": "08:00"},
                    {"start": "12:00", "end": "14:00"},
                    {"start": "22:00", "end": "02:00"},
                ],
            },
        },
    })

    assert result["enabled"] is True
    assert result["ranges"] == [
        {"start": "00:00", "end": "08:00"},
        {"start": "12:00", "end": "14:00"},
        {"start": "22:00", "end": "02:00"},
    ]


def test_invalid_and_zero_length_ranges_are_ignored():
    result = build_off_peak_badge_config({
        "dashboard": {
            "off_peak_badge": {
                "ranges": [
                    {"start": "25:00", "end": "08:00"},
                    {"start": "09:00", "end": "09:00"},
                    {"start": "10:00", "end": "11:30"},
                    "not-a-range",
                ],
            },
        },
    })

    assert result["ranges"] == [{"start": "10:00", "end": "11:30"}]


def test_explicit_empty_range_list_disables_off_peak_windows():
    result = build_off_peak_badge_config({
        "dashboard": {"off_peak_badge": {"ranges": []}},
    })

    assert result == {"enabled": True, "ranges": []}
