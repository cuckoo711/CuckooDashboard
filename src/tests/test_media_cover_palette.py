"""Tests for backend cover color payloads consumed by /music."""

from __future__ import annotations

from services.media_service import (
    _clone_cover_palette,
    _contrast_rgb_from_spectrum,
    _inverse_rgb_from_theme,
    _palette_payload,
    _spectrum_rgb_from_theme,
)


VISUAL_KEYS = (
    "cover_palette_rgb",
    "cover_palette_1",
    "cover_palette_2",
    "cover_palette_3",
    "cover_theme_rgb",
    "cover_inverse_rgb",
    "spectrum_rgb",
    "spectrum_block_rgb",
)


def assert_rgb(value):
    assert isinstance(value, list)
    assert len(value) == 3
    assert all(isinstance(channel, int) for channel in value)
    assert all(0 <= channel <= 255 for channel in value)


def test_default_palette_includes_backend_visual_colors():
    payload = _clone_cover_palette()

    for key in VISUAL_KEYS:
        assert key in payload
        assert_rgb(payload[key])

    assert payload["cover_theme_rgb"] == payload["cover_palette_rgb"]
    assert payload["spectrum_rgb"] == _spectrum_rgb_from_theme(payload["cover_theme_rgb"])
    assert payload["spectrum_block_rgb"] == _inverse_rgb_from_theme(
        payload["cover_theme_rgb"], payload["spectrum_rgb"]
    )


def test_palette_payload_derives_spectrum_from_theme_complement_mid_color():
    payload = _palette_payload([(240, 48, 36), (24, 120, 220), (210, 180, 90)])

    assert payload["cover_palette_1"] == [240, 48, 36]
    assert payload["cover_palette_2"] == [24, 120, 220]
    assert payload["cover_palette_3"] == [210, 180, 90]
    assert payload["cover_theme_rgb"] == payload["cover_palette_rgb"]
    assert payload["spectrum_rgb"] == _spectrum_rgb_from_theme(payload["cover_theme_rgb"])
    assert payload["spectrum_block_rgb"] == _contrast_rgb_from_spectrum(payload["spectrum_rgb"])
    assert payload["cover_inverse_rgb"] == payload["spectrum_block_rgb"]

    for key in VISUAL_KEYS:
        assert_rgb(payload[key])


def test_explicit_backend_visual_colors_are_preserved_and_clamped():
    payload = _clone_cover_palette({
        "cover_palette_rgb": [10, 20, 30],
        "spectrum_rgb": [-5, 128.4, 999],
        "spectrum_block_rgb": [300, 12.2, "80"],
    })

    assert payload["cover_theme_rgb"] == [10, 20, 30]
    assert payload["spectrum_rgb"] == [0, 128, 255]
    assert payload["cover_inverse_rgb"] == [255, 12, 80]
    assert payload["spectrum_block_rgb"] == [255, 12, 80]
