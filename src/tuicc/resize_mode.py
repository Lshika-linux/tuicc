"""Interactive resize mode: pure functions for growing/shrinking or
repositioning one ModuleBox, one terminal cell at a time.

resize_step touches only w/h, move_step touches only x/y — layout.py's
boxes are independent ratios with no cross-box coordination (see its
docstring), so changing one box never moves or resizes another either
way — what you see after a resize/move is exactly what you get, same
as everywhere else in this layout system.

ResizeState/SpawnPickerState and the functions below them are the
session-level layer on top of that per-box math — what main.py's loop
used to hold as six/two loose local variables and inline key-handling.
Same "pure function over an explicit value" style as the rest of this
codebase (resolve_selection, next_module_name in navigation.py), not a
class-with-methods rewrite: a dataclass is just the state, every
function here takes one and mutates it, main.py still owns *when* to
call them.
"""

from dataclasses import dataclass, field

from tuicc.layout import ModuleBox
from tuicc.render_utils import draw_box_outline

STEP_CELLS = 1
MIN_CELLS = 3


def enter_resize(box: ModuleBox) -> dict:
    """Snapshots box's original x/y/w/h, for cancel_resize to restore —
    a single resize-mode session can change both size and position
    (toggled via move_toggle), so one Escape must undo all of it.
    """
    return {"x": box.x, "y": box.y, "w": box.w, "h": box.h}


def resize_step(box: ModuleBox, dimension: str, grow: bool,
                 term_width: int, term_height: int,
                 x_cells: int, y_cells: int) -> None:
    """Moves box.w or box.h by one terminal cell (as a ratio), clamped
    to a MIN_CELLS-cell minimum and to the terminal edge (can't grow
    past term_size - origin, since x/y aren't touched here).
    """
    if dimension == "w":
        term_size, origin, current = term_width, x_cells, box.w
    else:
        term_size, origin, current = term_height, y_cells, box.h

    delta = (STEP_CELLS if grow else -STEP_CELLS) / term_size
    min_ratio = MIN_CELLS / term_size
    max_ratio = (term_size - origin) / term_size
    new_value = min(max(current + delta, min_ratio), max_ratio)
    setattr(box, dimension, new_value)


def move_step(box: ModuleBox, dimension: str, grow: bool,
              term_width: int, term_height: int,
              w_cells: int, h_cells: int) -> None:
    """Moves box.x or box.y by one terminal cell (as a ratio), clamped
    to [0, term_size - own_size] so the box can't be dragged off-screen
    — own_size comes from the box's OWN current w/h, since x/y are the
    only things this function touches.
    """
    if dimension == "x":
        term_size, own_size_cells, current = term_width, w_cells, box.x
    else:
        term_size, own_size_cells, current = term_height, h_cells, box.y

    delta = (STEP_CELLS if grow else -STEP_CELLS) / term_size
    max_ratio = (term_size - own_size_cells) / term_size
    new_value = min(max(current + delta, 0.0), max_ratio)
    setattr(box, dimension, new_value)


def cancel_resize(box: ModuleBox, snapshot: dict) -> None:
    """Restores exactly what enter_resize snapshotted."""
    box.x = snapshot["x"]
    box.y = snapshot["y"]
    box.w = snapshot["w"]
    box.h = snapshot["h"]


@dataclass
class ResizeState:
    """active=True, editing=False: browsing — the session is open, no
    box is being resized/moved right now; normal navigation (arrows,
    Tab, switch_module) picks which module active_module refers to,
    same as outside the session entirely. active=True, editing=True:
    a specific box (box/snapshot/dimension/is_new_box populated) is
    being resized/moved, same behavior this whole module always had.
    """
    active: bool = False
    editing: bool = False
    box: ModuleBox | None = None
    snapshot: dict | None = None
    dimension: str = "size"
    is_new_box: bool = False
    confirm_delete: bool = False


def enter_edit_mode(state: ResizeState) -> None:
    """F2 from normal navigation — opens the session at the browsing
    level. Doesn't touch any box; confirm (via enter_box_editing)
    drills into whichever one is active_module at the time.
    """
    state.active = True
    state.editing = False
    state.box = None
    state.snapshot = None
    state.dimension = "size"
    state.is_new_box = False
    state.confirm_delete = False


def exit_edit_mode(state: ResizeState) -> None:
    """The only function that fully leaves the session — Escape or F3
    at the browsing level. (Escape/confirm at the editing level return
    to browsing instead — see commit_box_editing/escape_box_editing.)
    """
    state.active = False
    state.editing = False
    state.box = None
    state.snapshot = None
    state.dimension = "size"
    state.is_new_box = False
    state.confirm_delete = False


def enter_box_editing(state: ResizeState, box: ModuleBox, is_new: bool = False) -> None:
    """Drills into resizing/moving box — from browsing (confirm on the
    active module), or standalone (spawn_box/resize both still work
    directly from full normal navigation with no F2 first, same as
    before this module had a browsing level at all — hence force-
    setting active=True here rather than assuming it's already True).
    is_new marks a box the spawn picker just created (no "before"
    state to revert to — see escape_box_editing below) and starts it
    in move dimension, so a freshly spawned box is immediately
    positionable rather than resizable.
    """
    state.active = True
    state.editing = True
    state.box = box
    state.snapshot = enter_resize(box)
    state.dimension = "move" if is_new else "size"
    state.is_new_box = is_new
    state.confirm_delete = False


def commit_box_editing(state: ResizeState) -> None:
    """Ends the current box's edit, keeping its state (no revert) —
    same effect as pressing Enter — and returns to browsing, not out
    of the session (active stays True). Also used as the shared "wrap
    up before doing something else" step so F1/F3/F4/F6 can interrupt
    an in-progress edit instead of being swallowed by it — F3/F4 go on
    to call exit_edit_mode themselves afterward, since saving/cycling
    ends the whole session, not just the current box's edit.
    """
    state.editing = False
    state.box = None
    state.snapshot = None
    state.dimension = "size"
    state.is_new_box = False
    state.confirm_delete = False


def escape_box_editing(state: ResizeState, layout_boxes: list) -> None:
    """Discards the current box's edit — reverts via cancel_resize, or,
    if is_new_box, removes it from layout_boxes entirely (there's no
    "before" a just-spawned box existed) — then returns to browsing,
    same as commit_box_editing.
    """
    if state.is_new_box:
        layout_boxes.remove(state.box)
    else:
        cancel_resize(state.box, state.snapshot)
    commit_box_editing(state)


def request_delete(state: ResizeState, box: ModuleBox) -> None:
    """Starts a y/n delete confirmation for box — works from either
    level: at the editing level box is already state.box; at the
    browsing level the caller looks it up by active_module first.
    """
    state.box = box
    state.confirm_delete = True


def confirm_delete_yes(state: ResizeState, layout_boxes: list) -> None:
    """Removes state.box and returns to browsing — whether the delete
    was requested from browsing or from mid-edit, either way there's
    no longer a box to keep editing.
    """
    layout_boxes.remove(state.box)
    state.editing = False
    state.box = None
    state.snapshot = None
    state.dimension = "size"
    state.is_new_box = False
    state.confirm_delete = False


def confirm_delete_no(state: ResizeState) -> None:
    """Cancels the pending delete, resuming whichever level asked for
    it (editing's box/snapshot are untouched if that's where this
    came from).
    """
    state.confirm_delete = False


def apply_direction(state: ResizeState, direction: str,
                     term_width: int, term_height: int,
                     x_cells: int, y_cells: int, w_cells: int, h_cells: int) -> None:
    """A direction key while the session is active — resize_step or
    move_step depending on state.dimension, matching the current
    box's cell rect (x/y/w/h all needed since resize needs x/y as the
    clamp origin and move needs w/h as the box's own size).
    """
    grow = direction in ("right", "down")
    if state.dimension == "size":
        dim = "w" if direction in ("left", "right") else "h"
        resize_step(state.box, dim, grow, term_width, term_height, x_cells, y_cells)
    else:
        dim = "x" if direction in ("left", "right") else "y"
        move_step(state.box, dim, grow, term_width, term_height, w_cells, h_cells)


def toggle_dimension(state: ResizeState) -> None:
    state.dimension = "move" if state.dimension == "size" else "size"


def hint_text(state: ResizeState, active_module: str) -> str:
    if state.confirm_delete:
        return f"Delete {active_module}? y/n"
    if not state.editing:
        return (
            f"EDIT MODE — {active_module}  Enter edit this module  Del delete it  |  "
            f"switch_module/Tab/arrows pick another  |  F1 help  F3 save+exit  "
            f"F4 cycle preset  F6 spawn  Esc exit"
        )
    action = "resize" if state.dimension == "size" else "move"
    return (
        f"[{state.dimension.upper()}] {active_module} — ←→↑↓ {action}  "
        f"M switch  Del delete  Enter done (back to browsing)  Esc cancel  |  "
        f"F1 help  F3 save+exit  F4 cycle preset  F6 spawn"
    )


def draw_editing_highlight(stdscr, box, theme_pairs) -> None:
    """Redraws the box currently being edited with an urgent-colored
    outline, on top of whatever draw_all() already painted it with
    (its module's own border_selected logic, unchanged) — a post-frame
    overlay, same pattern render_utils.draw_status_line already is,
    not a RenderContext field or a per-module draw() change. Only
    meaningful while editing (a specific box, not just browsing).
    draw_box_outline already guards its own curses.error, no need to
    wrap it again here.
    """
    x, y, w, h = box
    draw_box_outline(stdscr, y, x, h, w, theme_pairs.get("urgent", 0))


@dataclass
class SpawnPickerState:
    active: bool = False
    choices: list = field(default_factory=list)


def open_picker(state: SpawnPickerState, available_names) -> None:
    """available_names is computed by the caller (main.py, from
    render.MODULES vs the current layout) — keeps this module from
    needing to import render.py. A no-op if nothing's available.
    """
    available = sorted(available_names)
    if available:
        state.choices = available[:9]
        state.active = True


def choose(state: SpawnPickerState, key: int) -> str | None:
    """The chosen module name for a digit keypress, or None if the key
    wasn't a valid choice — either way the picker closes (matches the
    existing behavior: any key exits it, not just a valid digit).
    """
    choice = None
    if ord("1") <= key < ord("1") + len(state.choices):
        choice = state.choices[key - ord("1")]
    state.active = False
    state.choices = []
    return choice


def spawn_hint_text(state: SpawnPickerState) -> str:
    choices = "  ".join(f"{i+1} {name}" for i, name in enumerate(state.choices))
    return f"SPAWN — {choices}  Esc cancel"
