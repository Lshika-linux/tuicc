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
    enter_edit_mode,
    exit_edit_mode,
    enter_box_editing,
    commit_box_editing,
    escape_box_editing,
    request_delete,
    confirm_delete_yes,
    confirm_delete_no,
    apply_direction,
    toggle_dimension,
    hint_text,
    SpawnPickerState,
    open_picker,
    choose,
    spawn_hint_text,
    EditKeyResult,
    handle_editing_key,
)


class _FakeConfig:
    """Just enough of Config to exercise handle_editing_key's key
    lookups — matches test_launcher.py's/test_actions.py's own
    _FakeConfig convention (arbitrary distinct ints, not real curses
    codes; only distinctness matters for branch selection)."""
    keybinds = {
        "confirm_yes": 1, "confirm_no": 2, "confirm": 3,
        "move_toggle": 4, "delete_box": 5,
        "spawn_box": 6, "resize": 7, "save_layout": 8,
        "cycle_preset": 9, "new_preset": 10, "help": 11,
    }

    def __init__(self, boxes=None):
        self.layout = _FakeLayout(boxes or [])


class _FakeLayout:
    def __init__(self, boxes):
        self.boxes = boxes


_DIRECTION_KEYS = {100: "right", 101: "left"}


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

    # approx, not exact == against the raw "0.2 - 1/40" expression: that
    # expression itself carries its own float noise (e.g.
    # 0.17500000000000002), which move_step's own rounding (see its
    # docstring) now deliberately does NOT reproduce.
    assert box.y == pytest.approx(0.175)


def test_move_step_never_touches_w_or_h():
    box = ModuleBox(name="sidebar", x=0.2, y=0.2, w=0.26, h=0.5)

    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.w == 0.26
    assert box.h == 0.5


def test_move_step_clamps_at_zero():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.5)

    move_step(box, "x", grow=False, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.x == 0.0


def test_move_step_does_not_accumulate_float_noise():
    # Found live: repeated arrow-key nudges (right then back left, the
    # same net-zero movement a bit of hands-on resize-mode dragging
    # easily produces) used to leave x at something like
    # 5.204170427930421e-18 instead of exactly 0.0 — invisible on
    # screen, but enough to break navigation.py's tab_order(), which
    # sorts boxes by exact (x, y) comparison: a box that's visually
    # flush with its neighbors but off by an epsilon sorts into its own
    # separate "column". term_width=131 (not a clean divisor of 1)
    # reproduces the same kind of binary-fraction residue the original
    # bug report's real terminal size did.
    box = ModuleBox(name="sessions", x=0.0, y=0.0, w=0.2, h=0.1)

    for _ in range(7):
        move_step(box, "x", grow=True, term_width=131, term_height=40, w_cells=26, h_cells=4)
    for _ in range(7):
        move_step(box, "x", grow=False, term_width=131, term_height=40, w_cells=26, h_cells=4)

    assert box.x == 0.0


def test_move_step_clamps_at_terminal_edge():
    # Box is 26 cells wide in a 100-wide terminal — x can grow at most
    # to 74 (100 - 26), so its right edge never runs off-screen.
    box = ModuleBox(name="sidebar", x=0.73, y=0.0, w=0.26, h=0.5)

    move_step(box, "x", grow=True, term_width=100, term_height=40, w_cells=26, h_cells=20)

    assert box.x == pytest.approx(0.74)


# ---------- ResizeState: enter_edit_mode / exit_edit_mode (browsing) ----------

def test_enter_edit_mode_opens_browsing_with_no_box():
    state = ResizeState()

    enter_edit_mode(state)

    assert state.active is True
    assert state.editing is False
    assert state.box is None


def test_exit_edit_mode_fully_resets_from_either_level():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)

    exit_edit_mode(state)

    assert state.active is False
    assert state.editing is False
    assert state.box is None
    assert state.snapshot is None


# ---------- ResizeState: enter_box_editing / commit_box_editing / escape_box_editing ----------

def test_enter_box_editing_snapshots_and_activates():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()

    enter_box_editing(state, box)

    assert state.active is True
    assert state.editing is True
    assert state.box is box
    assert state.snapshot == {"x": 0.1, "y": 0.2, "w": 0.26, "h": 0.6}
    assert state.dimension == "size"
    assert state.is_new_box is False


def test_enter_box_editing_works_standalone_without_prior_browsing():
    # spawn_box/resize both still work directly from full normal
    # navigation, with no enter_edit_mode first.
    box = ModuleBox(name="clock", x=0.4, y=0.4, w=0.2, h=0.2)
    state = ResizeState()

    enter_box_editing(state, box, is_new=True)

    assert state.active is True
    assert state.dimension == "move"
    assert state.is_new_box is True


def test_commit_box_editing_returns_to_browsing_and_keeps_box_changes():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)
    box.w = 0.5  # a change made during the session

    commit_box_editing(state)

    assert state.active is True  # still in the session, just browsing
    assert state.editing is False
    assert state.box is None
    assert state.snapshot is None
    assert box.w == 0.5  # not reverted


def test_escape_box_editing_reverts_an_existing_box_and_returns_to_browsing():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)
    box.w = 0.5

    escape_box_editing(state, layout_boxes=[box])

    assert box.w == 0.26
    assert state.active is True
    assert state.editing is False


def test_escape_box_editing_removes_a_just_spawned_box():
    box = ModuleBox(name="clock", x=0.4, y=0.4, w=0.2, h=0.2)
    state = ResizeState()
    enter_box_editing(state, box, is_new=True)
    layout_boxes = [box]

    escape_box_editing(state, layout_boxes)

    assert box not in layout_boxes
    assert state.active is True
    assert state.editing is False


def test_escape_box_editing_after_confirm_delete_pending_still_resets_it():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)
    state.confirm_delete = True

    escape_box_editing(state, layout_boxes=[box])

    assert state.confirm_delete is False


# ---------- ResizeState: request_delete / confirm_delete_yes / confirm_delete_no ----------

def test_request_delete_from_browsing_sets_box_and_pending():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_edit_mode(state)

    request_delete(state, box)

    assert state.box is box
    assert state.confirm_delete is True


def test_confirm_delete_yes_removes_box_and_returns_to_browsing():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)
    request_delete(state, box)
    layout_boxes = [box]

    confirm_delete_yes(state, layout_boxes)

    assert box not in layout_boxes
    assert state.editing is False
    assert state.box is None
    assert state.confirm_delete is False


def test_confirm_delete_no_cancels_pending_and_resumes_editing():
    box = ModuleBox(name="sidebar", x=0.1, y=0.2, w=0.26, h=0.6)
    state = ResizeState()
    enter_box_editing(state, box)
    request_delete(state, box)

    confirm_delete_no(state)

    assert state.confirm_delete is False
    assert state.editing is True  # resumed, not reset
    assert state.box is box


# ---------- ResizeState: apply_direction / toggle_dimension ----------

def test_apply_direction_size_dimension_resizes():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = ResizeState()
    enter_box_editing(state, box)

    apply_direction(state, "right", term_width=100, term_height=40, x_cells=0, y_cells=0, w_cells=50, h_cells=20)

    assert box.w == 0.51
    assert box.x == 0.0  # size dimension never touches position


def test_apply_direction_move_dimension_moves():
    box = ModuleBox(name="sidebar", x=0.2, y=0.0, w=0.26, h=0.5)
    state = ResizeState()
    enter_box_editing(state, box)
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


def test_hint_text_shows_browsing_hint_when_not_editing():
    state = ResizeState(editing=False)

    text = hint_text(state, "sidebar")

    assert "sidebar" in text
    assert "EDIT MODE" in text


def test_hint_text_shows_dimension_and_module_when_editing():
    state = ResizeState(editing=True, dimension="size")
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


# ---------- handle_editing_key ----------

def _editing_state(box):
    state = ResizeState()
    enter_box_editing(state, box)
    return state


def test_handle_editing_key_direction_key_resizes_and_keeps_claiming():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    cfg = _FakeConfig()
    boxes = {"sidebar": (0, 0, 50, 20)}

    result = handle_editing_key(state, 100, cfg, "sidebar", _DIRECTION_KEYS, boxes, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert box.w == 0.51


def test_handle_editing_key_move_toggle_flips_dimension():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    cfg = _FakeConfig()

    result = handle_editing_key(state, cfg.keybinds["move_toggle"], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert state.dimension == "move"


def test_handle_editing_key_delete_box_requests_confirmation():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    cfg = _FakeConfig()

    result = handle_editing_key(state, cfg.keybinds["delete_box"], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert state.confirm_delete is True


def test_handle_editing_key_confirm_commits_and_ends_claim():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    box.w = 0.7  # in-progress change
    cfg = _FakeConfig()

    result = handle_editing_key(state, cfg.keybinds["confirm"], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=False)
    assert state.editing is False  # back to browsing, not exited
    assert box.w == 0.7  # not reverted


def test_handle_editing_key_escape_reverts_and_ends_claim():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    box.w = 0.7
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, 27, cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=False)
    assert box.w == 0.5  # reverted


def test_handle_editing_key_confirm_delete_yes_returns_deleted_name():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    state.confirm_delete = True
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, cfg.keybinds["confirm_yes"], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=False, deleted_name="sidebar")
    assert box not in cfg.layout.boxes


def test_handle_editing_key_confirm_delete_via_plain_confirm_also_deletes():
    # confirm (Enter) is an accepted alternate to confirm_yes here too —
    # same pattern as every other Y/N site in the codebase.
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    state.confirm_delete = True
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, cfg.keybinds["confirm"], cfg, "sidebar", {}, {}, 100, 40)

    assert result.deleted_name == "sidebar"


def test_handle_editing_key_confirm_delete_no_cancels_and_keeps_claiming():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    state.confirm_delete = True
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, cfg.keybinds["confirm_no"], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert state.confirm_delete is False
    assert box in cfg.layout.boxes


def test_handle_editing_key_confirm_delete_any_other_key_leaves_it_pending():
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    state.confirm_delete = True
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, 999, cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert state.confirm_delete is True  # still pending, untouched


@pytest.mark.parametrize("keybind_name", [
    "spawn_box", "resize", "save_layout", "cycle_preset", "new_preset", "help",
])
def test_handle_editing_key_handoff_branches_commit_and_signal(keybind_name):
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    box.w = 0.7  # in-progress change — must survive the handoff (no revert)
    cfg = _FakeConfig(boxes=[box])

    result = handle_editing_key(state, cfg.keybinds[keybind_name], cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True, handoff=keybind_name)
    assert state.editing is False  # commit_box_editing ran — back to browsing
    assert box.w == 0.7  # not reverted


def test_handle_editing_key_unrecognized_key_keeps_claiming():
    # LOAD-BEARING: a stray key must not silently end the editing
    # session — see the matching comment on handle_editing_key itself.
    box = ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.5, h=0.5)
    state = _editing_state(box)
    cfg = _FakeConfig()

    result = handle_editing_key(state, 12345, cfg, "sidebar", {}, {}, 100, 40)

    assert result == EditKeyResult(still_claiming=True)
    assert state.editing is True
