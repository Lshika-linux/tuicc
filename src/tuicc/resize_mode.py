"""Interactive resize mode: pure functions for growing/shrinking or
repositioning one ModuleBox, one terminal cell at a time.

resize_step touches only w/h, move_step touches only x/y — layout.py's
boxes are independent ratios with no cross-box coordination (see its
docstring), so changing one box never moves or resizes another either
way — what you see after a resize/move is exactly what you get, same
as everywhere else in this layout system.
"""

from tuicc.layout import ModuleBox

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
