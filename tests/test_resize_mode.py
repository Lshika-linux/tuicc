"""Tests for resize_mode.py's pure enter_resize/resize_step/move_step/
cancel_resize (per-box math) and ResizeState/SpawnPickerState (the
session-level layer main.py's loop drives) — no curses, no I/O.
"""

import pytest

from tuicc.layout import ModuleBox
from tuicc.resize_mode import (
    enter_resize,
    resize_step,
    move_step,
    cancel_resize,
    ResizeState,
    start,
    commit,
    escape,
    apply_direction,
    toggle_dimension,
    hint_text,
    SpawnPickerState,
    open_picker,
    choose,
    spawn_hint_text,
)


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


# ---------- ResizeState: start / commit / escape ----------

def test_start_snapshots_and_activates():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()

    start(state, box)

    assert state.active is True
    assert state.box is box
    assert state.snapshot == {"x": 0.1, "y": 0.2, "w": 0.26, "h": 0.6}
    assert state.dimension == "size"
    assert state.is_new_box is False


def test_start_with_is_new_starts_in_move_dimension():
    box = ModuleBox(name="clock", x=0.4, y=0.4, w=0.2, h=0.2)
    state = ResizeState()

    start(state, box, is_new=True)

    assert state.dimension == "move"
    assert state.is_new_box is True


def test_commit_resets_state_but_keeps_box_changes():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    start(state, box)
    box.w = 0.5  # a change made during the session

    commit(state)

    assert state.active is False
    assert state.box is None
    assert state.snapshot is None
    assert box.w == 0.5  # not reverted


def test_escape_reverts_an_existing_box():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    start(state, box)
    box.w = 0.5

    escape(state, layout_boxes=[box])

    assert box.w == 0.26
    assert state.active is False


def test_escape_removes_a_just_spawned_box():
    box = ModuleBox(name="clock", x=0.4, y=0.4, w=0.2, h=0.2)
    state = ResizeState()
    start(state, box, is_new=True)
    layout_boxes = [box]

    escape(state, layout_boxes)

    assert box not in layout_boxes
    assert state.active is False


def test_escape_after_confirm_delete_pending_still_resets_it():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    start(state, box)
    state.confirm_delete = True

    escape(state, layout_boxes=[box])

    assert state.confirm_delete is False


# ---------- ResizeState: apply_direction / toggle_dimension ----------

def test_apply_direction_size_dimension_resizes():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = ResizeState()
    start(state, box)

    apply_direction(state, "right", term_width=100, term_height=40, x_cells=0, y_cells=0, w_cells=50, h_cells=20)

    assert box.w == 0.51
    assert box.x == 0.0  # size dimension never touches position


def test_apply_direction_move_dimension_moves():
    box = ModuleBox(name="sidebar", x=0.2, y=0.0, w=0.26, h=0.5)
    state = ResizeState()
    start(state, box)
    state.dimension = "move"

    apply_direction(state, "right", term_width=100, term_height=40, x_cells=20, y_cells=0, w_cells=26, h_cells=20)

    assert box.x == pytest.approx(0.21)
    assert box.w == 0.26  # move dimension never touches size


def test_toggle_dimension_flips_both_ways():
    state = ResizeState(dimension="size")

    toggle_dimension(state)
    assert state.dimension == "move"

    toggle_dimension(state)
    assert state.dimension == "size"


# ---------- ResizeState: hint_text ----------

def test_hint_text_shows_confirm_delete_prompt_when_pending():
    state = ResizeState(confirm_delete=True)

    assert hint_text(state, "sidebar") == "Delete sidebar? y/n"


def test_hint_text_shows_dimension_and_module():
    state = ResizeState(dimension="size")
    assert "SIZE" in hint_text(state, "sidebar")
    assert "sidebar" in hint_text(state, "sidebar")

    state.dimension = "move"
    assert "MOVE" in hint_text(state, "sidebar")


# ---------- SpawnPickerState ----------

def test_open_picker_activates_with_sorted_choices():
    state = SpawnPickerState()

    open_picker(state, {"sessions", "clock", "connectivity"})

    assert state.active is True
    assert state.choices == ["clock", "connectivity", "sessions"]


def test_open_picker_no_op_when_nothing_available():
    state = SpawnPickerState()

    open_picker(state, set())

    assert state.active is False
    assert state.choices == []


def test_open_picker_caps_at_nine_choices():
    state = SpawnPickerState()
    names = {f"module{i}" for i in range(12)}

    open_picker(state, names)

    assert len(state.choices) == 9


def test_choose_valid_digit_returns_the_name_and_closes():
    state = SpawnPickerState(active=True, choices=["clock", "sessions"])

    choice = choose(state, ord("2"))

    assert choice == "sessions"
    assert state.active is False
    assert state.choices == []


def test_choose_invalid_digit_returns_none_and_still_closes():
    state = SpawnPickerState(active=True, choices=["clock"])

    choice = choose(state, ord("9"))

    assert choice is None
    assert state.active is False


def test_spawn_hint_text_lists_numbered_choices():
    state = SpawnPickerState(active=True, choices=["clock", "sessions"])

    text = spawn_hint_text(state)

    assert "1 clock" in text
    assert "2 sessions" in text
