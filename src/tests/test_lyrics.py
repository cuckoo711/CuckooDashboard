"""Unit tests for lyrics parsing and matching logic."""

from __future__ import annotations

import pytest

from services.media_service import (
    _has_extra_junk,
    _is_fake_artist_variant,
    _parse_lrc,
    _parse_yrc,
    _strip_paren,
)


# ── _parse_lrc ──


class TestParseLrc:
    def test_basic(self):
        result = _parse_lrc("[01:23.45]Hello world")
        assert len(result) == 1
        assert abs(result[0][0] - (1 * 60 + 23.45)) < 0.01
        assert result[0][1] == "Hello world"

    def test_multiple_timestamps(self):
        result = _parse_lrc("[01:00.00][02:00.00]Repeat")
        assert len(result) == 2
        assert abs(result[0][0] - 60) < 0.01
        assert abs(result[1][0] - 120) < 0.01

    def test_empty_line(self):
        assert _parse_lrc("") == []

    def test_no_text(self):
        assert _parse_lrc("[01:00.00]") == []

    def test_sorting(self):
        result = _parse_lrc("[02:00.00]B\n[01:00.00]A")
        assert result[0][1] == "A"
        assert result[1][1] == "B"

    def test_integer_seconds(self):
        result = _parse_lrc("[00:30]Half")
        assert abs(result[0][0] - 30) < 0.01


# ── _parse_yrc ──


class TestParseYrc:
    def test_basic(self):
        yrc = "[16210,3460](16210,670,0)还(16880,410,0)没"
        result = _parse_yrc(yrc)
        assert len(result) == 1
        assert result[0]["start"] == 16210
        assert len(result[0]["chars"]) == 2
        assert result[0]["chars"][0]["text"] == "还"
        assert result[0]["chars"][0]["start"] == 16210
        assert result[0]["chars"][0]["dur"] == 670

    def test_skip_json_metadata(self):
        yrc = '{"t":0,"c":[]}\n[16210,3460](16210,670,0)Test'
        result = _parse_yrc(yrc)
        assert len(result) == 1

    def test_empty(self):
        assert _parse_yrc("") == []
        assert _parse_yrc(None) == []

    def test_no_header(self):
        result = _parse_yrc("(16210,670,0)还")
        assert result == []


# ── _strip_paren ──


class TestStripParen:
    def test_plain(self):
        assert _strip_paren("Hello") == "hello"

    def test_paren(self):
        assert _strip_paren("起风了 (Acoustic)") == "起风了"

    def test_chinese_paren(self):
        assert _strip_paren("起风了 （Live版）") == "起风了"

    def test_empty(self):
        assert _strip_paren("") == ""
        assert _strip_paren(None) == ""


# ── _is_fake_artist_variant ──


class TestIsFakeArtist:
    def test_suffix_dash(self):
        assert _is_fake_artist_variant("周杰伦-", "周杰伦") is True

    def test_suffix_dot(self):
        assert _is_fake_artist_variant("周杰伦.", "周杰伦") is True

    def test_real_duo(self):
        assert _is_fake_artist_variant("冯沁苑(买辣椒也用券)", "冯沁苑") is False

    def test_exact_match(self):
        assert _is_fake_artist_variant("周杰伦", "周杰伦") is False

    def test_empty(self):
        assert _is_fake_artist_variant("", "周杰伦") is False
        assert _is_fake_artist_variant("周杰伦", "") is False


# ── _has_extra_junk ──


class TestHasExtraJunk:
    def test_junk_in_candidate_not_target(self):
        assert _has_extra_junk("起风了 (Acoustic)", "起风了") is True

    def test_junk_in_both(self):
        assert _has_extra_junk("起风了 (Acoustic)", "起风了 (Acoustic)") is False

    def test_no_junk(self):
        assert _has_extra_junk("起风了", "起风了") is False

    def test_dj(self):
        assert _has_extra_junk("xxx DJ Remix", "xxx") is True
