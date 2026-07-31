"""Tests for render_utils.py — only the pure, curses-free helpers.
draw_box_outline/draw_filled_box need a real curses screen to test
meaningfully, so they're left untested here.
"""

from tuicc.render_utils import format_shortcut, centered_x


def test_format_shortcut_ctrl_combo():
    assert format_shortcut("Ctrl+L") == "[^L]"


def test_format_shortcut_ctrl_combo_is_uppercased():
    assert format_shortcut("Ctrl+l") == "[^L]"


def test_format_shortcut_non_ctrl_falls_back_to_brackets():
    assert format_shortcut("Escape") == "[Escape]"


def test_centered_x_even_padding():
    # box_x=0, box_w=10, text of length 4 -> 6 padding, 3 each side
    assert centered_x(0, 10, "abcd") == 3


def test_centered_x_odd_padding_rounds_down():
    # box_w=10, text length 3 -> 7 padding, //2 = 3
    assert centered_x(0, 10, "abc") == 3


def test_centered_x_text_wider_than_box_clamps_to_box_x():
    assert centered_x(5, 4, "toolongtext") == 5


def test_centered_x_respects_box_x_offset():
    assert centered_x(20, 10, "abcd") == 23
