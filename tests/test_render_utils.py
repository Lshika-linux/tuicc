"""Tests for render_utils.py — only the pure, curses-free helpers.
draw_box_outline/draw_filled_box need a real curses screen to test
meaningfully, so they're left untested here.
"""

from tuicc.render_utils import format_shortcut, centered_x, eighth_block_level, split_lines_into_columns


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


# ---------- split_lines_into_columns ----------
# draw_centered_lines' own overflow-handling math, pulled out pure —
# see that function's own docstring for the live bug this fixes (a
# 74-line diagnostics breakdown writing its first line onto the box's
# own top border).

def _lines(n):
    return [(f"line{i}", 0) for i in range(n)]


def test_split_lines_into_columns_fits_entirely_in_left_when_under_capacity():
    left, right = split_lines_into_columns(_lines(3), max_rows=5)
    assert len(left) == 3
    assert right == []


def test_split_lines_into_columns_splits_evenly_at_max_rows():
    left, right = split_lines_into_columns(_lines(8), max_rows=5)
    assert len(left) == 5
    assert len(right) == 3
    assert left[0] == ("line0", 0)
    assert right[0] == ("line5", 0)


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
