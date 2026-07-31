"""Tests for layout_engine.py — pure per-box ratio/rows -> absolute cell
math. No coordination between boxes: what a box specifies is exactly
what it gets, regardless of what its neighbors do.
"""

import pytest

from tuicc.layout import Layout, ModuleBox
from tuicc.layout_engine import compute_boxes


def test_ratio_h_scales_with_terminal_height():
    layout = Layout(boxes=[ModuleBox(name="a", x=0.0, y=0.0, w=1.0, h=0.5)])
    assert compute_boxes(layout, 100, 20)["a"] == (0, 0, 100, 10)
    assert compute_boxes(layout, 100, 40)["a"] == (0, 0, 100, 20)


def test_rows_gives_exact_row_count_regardless_of_terminal_height():
    layout = Layout(boxes=[ModuleBox(name="a", x=0.0, y=0.0, w=1.0, rows=6)])
    for term_h in (20, 24, 30, 50, 100):
        boxes = compute_boxes(layout, 100, term_h)
        assert boxes["a"][3] == 6


def test_x_y_w_are_always_ratios_even_when_rows_is_set():
    layout = Layout(boxes=[ModuleBox(name="a", x=0.25, y=0.5, w=0.5, rows=6)])
    boxes = compute_boxes(layout, 100, 40)
    x, y, w, h = boxes["a"]
    assert (x, y, w, h) == (25, 20, 50, 6)


def test_boxes_do_not_coordinate_with_each_other():
    # A "rows"-based box does not shrink or redistribute space to its
    # neighbors — each box is computed purely from its own fields. If two
    # boxes overlap or leave a gap because of how they were configured,
    # that's on the preset author, not the engine.
    layout = Layout(boxes=[
        ModuleBox(name="top", x=0.0, y=0.0, w=1.0, h=0.6),
        ModuleBox(name="bottom", x=0.0, y=0.6, w=1.0, rows=6),
    ])
    boxes_short = compute_boxes(layout, 100, 20)
    boxes_tall = compute_boxes(layout, 100, 100)
    # top's height changes with terminal height...
    assert boxes_short["top"][3] == 12
    assert boxes_tall["top"][3] == 60
    # ...but bottom's does not, and its y position is unaffected by top's size.
    assert boxes_short["bottom"][3] == 6
    assert boxes_tall["bottom"][3] == 6
    assert boxes_short["bottom"][1] == 12  # round(0.6 * 20), not top's actual bottom edge
    assert boxes_tall["bottom"][1] == 60


def test_multiple_boxes_computed_independently():
    layout = Layout(boxes=[
        ModuleBox(name="left", x=0.0, y=0.0, w=0.5, h=1.0),
        ModuleBox(name="right", x=0.5, y=0.0, w=0.5, h=1.0),
    ])
    boxes = compute_boxes(layout, 100, 40)
    assert boxes["left"] == (0, 0, 50, 40)
    assert boxes["right"] == (50, 0, 50, 40)


def test_real_preset_1_power_menu_is_always_six_rows():
    from tuicc.config import build_layout_from_preset

    layout = build_layout_from_preset(1)
    for term_h in (24, 30, 50):
        boxes = compute_boxes(layout, term_width=100, term_height=term_h)
        assert boxes["power_menu"][3] == 6
