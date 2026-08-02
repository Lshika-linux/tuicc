"""Tests for resize_mode.py's pure enter_resize/resize_step/move_step/
cancel_resize — no curses, no I/O, just ModuleBox mutation and the
math around it.
"""

import pytest

from tuicc.layout import ModuleBox
from tuicc.resize_mode import enter_resize, resize_step, move_step, cancel_resize


# ---------- enter_resize / cancel_resize ----------

def test_enter_resize_snapshots_x_y_w_h():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)

    snapshot = enter_resize(box)

    assert snapshot == {"x": 0.1, "y": 0.2, "w": 0.26, "h": 0.6}


def test_cancel_resize_restores_the_snapshot():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)

    snapshot = enter_resize(box)
    resize_step(box, "w", grow=True, term_width=100, term_height=40, x_cells=10, y_cells=8)
    resize_step(box, "h", grow=False, term_width=100, term_height=40, x_cells=10, y_cells=8)
    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=24)
    move_step(box, "y", grow=False, term_width=100, term_height=40, w_cells=26, h_cells=24)

    cancel_resize(box, snapshot)

    assert box.x == 0.1
    assert box.y == 0.2
    assert box.w == 0.26
    assert box.h == 0.6


# ---------- resize_step ----------

def test_resize_step_grows_width_by_one_cell():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)

    resize_step(box, "w", grow=True, term_width=100, term_height=40, x_cells=0, y_cells=0)

    assert box.w == 0.51


def test_resize_step_shrinks_height_by_one_cell():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)

    resize_step(box, "h", grow=False, term_width=100, term_height=40, x_cells=0, y_cells=0)

    assert box.h == 0.5 - 1 / 40


def test_resize_step_never_touches_x_or_y():
    box = ModuleBox(name="sidebar", x=0.3, y=0.4, w=0.5, h=0.5)

    resize_step(box, "w", grow=True, term_width=100, term_height=40, x_cells=30, y_cells=16)

    assert box.x == 0.3
    assert box.y == 0.4


def test_resize_step_clamps_at_minimum():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=3 / 100, h=0.5)

    resize_step(box, "w", grow=False, term_width=100, term_height=40, x_cells=0, y_cells=0)

    assert box.w == 3 / 100


def test_resize_step_clamps_at_terminal_edge():
    # Box starts at x_cells=90 in a 100-wide terminal — can grow at most
    # to fill the remaining 10 cells, since x itself is never touched.
    box = ModuleBox(name="clock", x=0.9, y=0.0, w=0.09, h=0.5)

    resize_step(box, "w", grow=True, term_width=100, term_height=40, x_cells=90, y_cells=0)

    assert box.w == pytest.approx(0.1)  # (100 - 90) / 100


# ---------- move_step ----------

def test_move_step_moves_x_by_one_cell():
    box = ModuleBox(name="sidebar", x=0.2, y=0.0, w=0.26, h=0.5)

    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.x == pytest.approx(0.21)


def test_move_step_moves_y_by_one_cell():
    box = ModuleBox(name="sidebar", x=0.0, y=0.2, w=0.26, h=0.5)

    move_step(box, "y", grow=False, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.y == 0.2 - 1 / 40


def test_move_step_never_touches_w_or_h():
    box = ModuleBox(name="sidebar", x=0.2, y=0.2, w=0.26, h=0.5)

    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.w == 0.26
    assert box.h == 0.5


def test_move_step_clamps_at_zero():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.5)

    move_step(box, "x", grow=False, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.x == 0.0


def test_move_step_clamps_at_terminal_edge():
    # Box is 26 cells wide in a 100-wide terminal — x can grow at most
    # to 74 (100 - 26), so its right edge never runs off-screen.
    box = ModuleBox(name="sidebar", x=0.73, y=0.0, w=0.26, h=0.5)

    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.x == pytest.approx(0.74)
