"""Tests for theme.py — pure color resolution, no curses needed."""

import pytest

from tuicc.theme import resolve_color, hex_to_curses_color, rgb_to_curses_color, nearest_level_index


def test_inherit_returns_minus_one():
    assert resolve_color("inherit") == -1


def test_named_color():
    assert resolve_color("cyan") == 6
    assert resolve_color("red") == 1


def test_hex_string():
    # Pure white should map to the top of the color cube: index 16 + 5*36 + 5*6 + 5 = 231
    assert resolve_color("#ffffff") == 231


def test_hex_and_named_agree_for_same_color():
    # "red" as a named color is index 1; as a hex value it goes through
    # the 256-color cube approximation instead, so they are NOT expected
    # to be equal — this test documents that distinction rather than
    # assuming they'd match.
    named = resolve_color("red")
    hexed = resolve_color("#ff0000")
    assert named == 1
    assert hexed != named  # different resolution paths, different numbers


def test_rgb_list():
    assert resolve_color([255, 255, 255]) == 231


def test_rgb_list_black():
    assert resolve_color([0, 0, 0]) == 16


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        resolve_color("not-a-color")


def test_invalid_value_raises_for_bad_type():
    with pytest.raises(ValueError):
        resolve_color(123)


def test_nearest_level_index_exact_matches():
    assert nearest_level_index(0) == 0
    assert nearest_level_index(95) == 1
    assert nearest_level_index(255) == 5


def test_nearest_level_index_rounds_to_closer_neighbor():
    # Levels are [0, 95, 135, 175, 215, 255]. 100 is closer to 95 than to 135.
    assert nearest_level_index(100) == 1
    # 130 is closer to 135 than to 95.
    assert nearest_level_index(130) == 2


def test_hex_to_curses_color_strips_leading_hash():
    assert hex_to_curses_color("#000000") == rgb_to_curses_color((0, 0, 0))


def test_hex_to_curses_color_without_hash_prefix():
    # lstrip("#") is a no-op if there's no "#", so this should still work.
    assert hex_to_curses_color("000000") == rgb_to_curses_color((0, 0, 0))


def test_rgb_to_curses_color_cube_formula():
    # Spot-check the formula directly for a known midpoint color.
    # (135, 135, 135) is exactly level index 2 on each channel.
    assert rgb_to_curses_color((135, 135, 135)) == 16 + (2 * 36) + (2 * 6) + 2
