"""Tests for render_utils.py — only the pure, curses-free helpers.
draw_box_outline/draw_filled_box need a real curses screen to test
meaningfully, so they're left untested here.
"""

from tuicc.render_utils import (
    format_shortcut,
    centered_x,
    eighth_block_level,
    split_lines_into_columns,
    _widest_line,
    display_width,
    wc_truncate,
    marquee_text,
    wrap_text,
)


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


# ---------- eighth_block_level ----------
# Same behavior media.py's own (now-deleted) _cava_row_level had — see
# test_media_module.py's own copies of these cases, still passing
# unchanged against the thin wrapper that delegates here now.

def test_eighth_block_level_bottommost_row_gets_low_bits():
    # a full-height value (100/100) fills the bottommost row (row_idx=1
    # of 2) completely — level 8, the max a single row can show
    assert eighth_block_level(value=100, max_value=100, row_idx=1, num_rows=2) == 8


def test_eighth_block_level_topmost_row_only_lights_once_bar_is_tall_enough():
    # a barely-nonzero value doesn't reach all the way up to the topmost
    # row when there's more than one row available
    assert eighth_block_level(value=1, max_value=100, row_idx=0, num_rows=2) == 0


def test_eighth_block_level_zero_value_is_zero_everywhere():
    assert eighth_block_level(value=0, max_value=100, row_idx=0, num_rows=3) == 0
    assert eighth_block_level(value=0, max_value=100, row_idx=2, num_rows=3) == 0


def test_eighth_block_level_scales_to_however_many_rows_are_available():
    # the SAME value read from the topmost row of a 1-row bar vs a 4-row
    # bar must land in different level buckets — more rows means finer
    # resolution, not the same absolute level regardless of height
    one_row = eighth_block_level(value=50, max_value=100, row_idx=0, num_rows=1)
    four_rows = eighth_block_level(value=50, max_value=100, row_idx=0, num_rows=4)
    assert one_row != four_rows


def test_eighth_block_level_num_rows_zero_is_zero_not_a_crash():
    assert eighth_block_level(value=100, max_value=100, row_idx=0, num_rows=0) == 0


def test_eighth_block_level_max_value_zero_is_zero_not_a_crash():
    # a bar with nothing to compare against (e.g. a domain that hasn't
    # reported a max yet) must not raise ZeroDivisionError
    assert eighth_block_level(value=0, max_value=0, row_idx=0, num_rows=2) == 0


def test_eighth_block_level_finer_resolution_than_whole_cells():
    # the whole point: two nearby values landing in DIFFERENT eighth-
    # block levels within the SAME single terminal row, not just the
    # same one-of-num_rows whole-cell step — this is what fixes swcc's
    # own "bars jump inconsistently with the real percentage" complaint
    low = eighth_block_level(value=40, max_value=100, row_idx=0, num_rows=1)
    high = eighth_block_level(value=60, max_value=100, row_idx=0, num_rows=1)
    assert low != high


def test_centered_x_text_wider_than_box_clamps_to_box_x():
    assert centered_x(5, 4, "toolongtext") == 5


def test_centered_x_respects_box_x_offset():
    assert centered_x(20, 10, "abcd") == 23


# ---------- _widest_line ----------
# draw_centered_lines' own 2-column block-centering helper — the
# widest (already truncated) line in a column, so each column can be
# centered within its own half-width as one unit rather than hugging
# a fixed left margin regardless of how much of it the content
# actually uses.

def test_widest_line_empty_is_empty_string():
    assert _widest_line([], max_w=10) == ""


def test_widest_line_picks_the_longest():
    lines = [("short", 0), ("a much longer line", 0), ("mid", 0)]

    assert _widest_line(lines, max_w=50) == "a much longer line"


def test_widest_line_respects_truncation():
    # wc_truncate() clips, no "..." suffix — matching what
    # _draw_centered_column will actually draw.
    lines = [("short", 0), ("way too long for the column", 0)]

    assert _widest_line(lines, max_w=10) == "way too lo"


# ---------- split_lines_into_columns ----------
# draw_centered_lines' own overflow-handling math, pulled out pure —
# see that function's own docstring for the live bug this fixes (a
# 74-line diagnostics breakdown writing its first line onto the box's
# own top border).

def _lines(n):
    return [(f"line{i}", 0) for i in range(n)]


def test_split_lines_into_columns_balances_a_small_input():
    # Balanced (left gets the extra one on an odd count), not "fill
    # left to max_rows first" — this exact combo (3 lines, room for 5)
    # never actually reaches this function via draw_centered_lines
    # itself (which only calls it once len(lines) > max_rows), but the
    # pure math should still be internally consistent.
    left, right = split_lines_into_columns(_lines(3), max_rows=5)
    assert len(left) == 2
    assert len(right) == 1


def test_split_lines_into_columns_splits_evenly_at_max_rows():
    left, right = split_lines_into_columns(_lines(8), max_rows=5)
    assert len(left) == 4
    assert len(right) == 4
    assert left[0] == ("line0", 0)
    assert right[0] == ("line4", 0)


def test_split_lines_into_columns_truncates_with_a_more_marker_past_capacity():
    # capacity = max_rows * 2 = 10; 15 lines is 5 over.
    left, right = split_lines_into_columns(_lines(15), max_rows=5)
    total_shown = left + right
    assert len(total_shown) == 10  # never more than 2 full columns
    assert total_shown[-1][0] == "+6 more"  # 9 real lines shown + 1 marker slot = 10, 15-9=6 hidden


def test_split_lines_into_columns_more_marker_reuses_last_lines_own_color():
    # capacity = max_rows * 2 = 2; 3 lines is 1 over, so the marker
    # takes the last of the 2 available slots.
    lines = [("line0", 1), ("line1", 2), ("line2", 3)]
    left, right = split_lines_into_columns(lines, max_rows=1)
    total_shown = left + right
    assert total_shown[-1] == ("+2 more", 1)  # color 1 = the last SHOWN real line's own color (line0)


def test_split_lines_into_columns_empty_input():
    assert split_lines_into_columns([], max_rows=5) == ([], [])


def test_split_lines_into_columns_zero_max_rows_returns_empty():
    assert split_lines_into_columns(_lines(3), max_rows=0) == ([], [])


# ---------- display_width / wc_truncate ----------
# Real, live-confirmed wcwidth() behavior (this session) — a variation-
# selector-16 emoji sequence (base char + U+FE0F) reports 2 real
# columns via wcswidth() on the whole sequence, NOT the sum of each
# character's own wcwidth() (1 + 0 = 1, wrong — confirmed live this
# undercounts). See CLAUDE/NOTES/design-decisions.md
# #rwb-wide-character-corruption for why this class of bug is worse
# than "looks a bit off".

def test_display_width_plain_ascii_matches_len():
    assert display_width("hello") == len("hello")


def test_display_width_vs16_emoji_sequence_is_2_not_len():
    # "☀️" is base U+2600 + U+FE0F — len() is 2 (two codepoints) but
    # that's coincidence, not correctness: per-character wcwidth() sums
    # to 1 (1 + 0), the real rendered width is 2.
    assert display_width("☀️") == 2


def test_display_width_already_wide_emoji_no_vs16_needed():
    # "⛅" (U+26C5) is a single codepoint, already default-emoji-width
    # per Unicode — no VS16 involved, len() == 1 but display width == 2.
    assert display_width("⛅") == 2


def test_display_width_empty_string():
    assert display_width("") == 0


# CJK/fullwidth cases — ported from the now-retired text_width.py
# (unicodedata.east_asian_width()-based; see CLAUDE/NOTES/
# design-decisions.md#text-width-consolidation) once display_width()/
# wc_truncate() here were confirmed to be a strict superset: wcswidth()
# correctly classifies East Asian Wide/Fullwidth characters as 2
# columns too, not just VS16 sequences, so nothing was lost by
# consolidating onto one implementation.

def test_display_width_counts_cjk_chars_as_two():
    assert display_width("和氣") == 4


def test_display_width_mixed_ascii_and_cjk():
    # "CV. " (4 narrow, 4 cols) + "和氣あず未" (5 wide chars, 10 cols)
    assert display_width("CV. 和氣あず未") == 14


def test_wc_truncate_plain_ascii_same_as_slicing():
    assert wc_truncate("hello world", 5) == "hello"


def test_wc_truncate_never_splits_a_base_char_from_its_vs16():
    # Budget for 1 column: the base char alone (1 col via its own bare
    # wcwidth) would "fit", but keeping it without its VS16 would
    # silently change which presentation form renders — dropped whole
    # instead, matching how the sequence is measured as one 2-column
    # unit everywhere else.
    assert wc_truncate("☀️", 1) == ""
    assert wc_truncate("☀️", 2) == "☀️"


def test_wc_truncate_never_splits_a_cjk_char():
    # "和" is 2 columns; a budget of 1 can't fit it at all.
    assert wc_truncate("和x", 1) == ""
    assert wc_truncate("和x", 2) == "和"
    assert wc_truncate("和x", 3) == "和x"


def test_wc_truncate_stops_at_exact_cjk_char_boundary():
    # Each of these two chars is 2 columns; budget of 4 fits both exactly.
    assert wc_truncate("和氣あ", 4) == "和氣"


def test_wc_truncate_budget_zero_returns_empty():
    assert wc_truncate("hello", 0) == ""


def test_wc_truncate_negative_budget_returns_empty():
    assert wc_truncate("anything", -5) == ""


def test_wc_truncate_fits_whole_string_returns_it_unchanged():
    assert wc_truncate("hi", 10) == "hi"


# ---------- marquee_text ----------
# Originally modules/media.py's own (Now Playing's artist/title
# scroll) — moved here, unchanged, once modules/sidebar.py needed the
# same behavior for an overflowing window title.

def test_marquee_text_returns_as_is_when_it_fits():
    assert marquee_text("Short", width=20, now=0.0) == "Short"


def test_marquee_text_returns_exactly_width_chars_when_too_long():
    result = marquee_text("A very long title that overflows", width=10, now=0.0)
    assert len(result) == 10


def test_marquee_text_scrolls_over_time():
    text = "A very long title that overflows"
    first = marquee_text(text, width=10, now=0.0)
    later = marquee_text(text, width=10, now=10.0)  # many steps later
    assert first != later


def test_marquee_text_zero_width_returns_empty():
    assert marquee_text("anything", width=0, now=0.0) == ""


def test_marquee_text_fits_check_uses_display_width_not_char_count():
    # 8 CJK characters = 16 terminal columns, well over width=10 despite
    # len(text) == 8 passing a plain len()-based fits check.
    text = "スペシャルウィーク"[:8]
    result = marquee_text(text, width=10, now=0.0)
    assert result != text


def test_marquee_text_result_never_exceeds_width_in_columns_for_cjk_text():
    text = "スペシャルウィーク（CV. 和氣あず未）"
    for now in (0.0, 1.0, 5.0, 12.3):
        result = marquee_text(text, width=10, now=now)
        assert display_width(result) <= 10


def test_marquee_text_never_splits_a_wide_character():
    text = "和氣あず未" * 3
    result = marquee_text(text, width=7, now=2.0)
    # Every character in a valid result must be one of the source
    # glyphs — a split wide character would produce something outside
    # that set (there's no such thing as half a glyph on a terminal).
    assert all(ch in text + "   " for ch in result)
    assert display_width(result) <= 7


# ---------- wrap_text ----------
# Built for modules/preview.py's per-window label — multi-line
# word-wrap, the counterpart to wc_truncate's single-line clip.

def test_wrap_text_returns_single_line_when_it_fits():
    assert wrap_text("short title", 20) == ["short title"]


def test_wrap_text_splits_on_word_boundaries():
    lines = wrap_text("one two three four", 8)
    assert lines == ["one two", "three", "four"]
    assert all(display_width(line) <= 8 for line in lines)


def test_wrap_text_zero_width_returns_single_empty_line():
    assert wrap_text("anything", 0) == [""]


def test_wrap_text_empty_string_returns_single_empty_line():
    assert wrap_text("", 10) == [""]


def test_wrap_text_hard_splits_a_single_word_longer_than_width():
    # A URL-shaped title with no spaces at all — must still be bounded
    # by width per line, not left overflowing.
    lines = wrap_text("averylongsingletokenwithnospaces", 10)
    assert all(display_width(line) <= 10 for line in lines)
    assert "".join(lines) == "averylongsingletokenwithnospaces"


def test_wrap_text_never_exceeds_width_for_cjk_text():
    text = "和氣あず未 スペシャルウィーク"
    lines = wrap_text(text, 6)
    assert all(display_width(line) <= 6 for line in lines)


def test_wrap_text_single_cjk_char_narrower_than_one_column_stops_without_looping():
    # width=1 can't fit even one 2-column CJK character — must degrade
    # (leave the unsplittable remainder as its own line) rather than
    # loop forever trying to shrink a word that never gets narrower.
    lines = wrap_text("和", 1)
    assert lines == ["和"]


def test_centered_x_wide_emoji_centers_correctly_not_by_codepoint_count():
    # "⛅" is 1 codepoint (len()==1) but 2 real columns (display_width,
    # tested above) — box_w=11 is chosen specifically because it's a
    # case where the two disagree: len()-based padding (11-1=10, //2=5)
    # gives a different result than the correct display-width-based
    # padding (11-2=9, //2=4) — box_w=10 would coincidentally give the
    # same answer either way (9//2 == 8//2 == 4), which wouldn't have
    # caught a regression back to len().
    assert centered_x(0, 11, "⛅") == 4

