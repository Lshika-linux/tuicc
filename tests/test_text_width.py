"""Tests for text_width.py's display_width()/truncate_to_width()."""

from tuicc.text_width import char_width, display_width, truncate_to_width


def test_char_width_ascii_is_one():
    assert char_width("a") == 1
    assert char_width(" ") == 1


def test_char_width_cjk_is_two():
    assert char_width("和") == 2
    assert char_width("ス") == 2  # katakana


def test_display_width_ascii_matches_len():
    assert display_width("hello") == 5


def test_display_width_counts_wide_chars_as_two():
    assert display_width("和氣") == 4


def test_display_width_mixed_ascii_and_wide():
    # "CV. " (4 narrow, 4 cols) + "和氣あず未" (5 wide chars, 10 cols)
    assert display_width("CV. 和氣あず未") == 14


def test_display_width_empty_string_is_zero():
    assert display_width("") == 0


def test_truncate_to_width_ascii_behaves_like_slicing():
    assert truncate_to_width("hello world", 5) == "hello"


def test_truncate_to_width_text_shorter_than_budget_is_unchanged():
    assert truncate_to_width("hi", 10) == "hi"


def test_truncate_to_width_never_splits_a_wide_char():
    # "和" is 2 columns; a budget of 1 can't fit it at all.
    assert truncate_to_width("和x", 1) == ""
    assert truncate_to_width("和x", 2) == "和"
    assert truncate_to_width("和x", 3) == "和x"


def test_truncate_to_width_zero_or_negative_budget_is_empty():
    assert truncate_to_width("anything", 0) == ""
    assert truncate_to_width("anything", -5) == ""


def test_truncate_to_width_stops_at_exact_wide_char_boundary():
    # Each of these two chars is 2 columns; budget of 4 fits both exactly.
    assert truncate_to_width("和氣あ", 4) == "和氣"
