"""Preview module: shows windows of the currently focused workspace.

No navigable items yet — previews are read-only for now.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


def draw(stdscr, box, state, selected_id=None):
    x, y, w, h = box

    draw_box_outline(stdscr, y, x, h, w)

    focused_region = None
    for region in state.regions:
        if region.id == state.focused_region_id:
            focused_region = region

    if focused_region is None:
        return

    for window in focused_region.windows:
        rx, ry, rw, rh = window.rect

        win_x = x + 1 + round(rx * (w - 2))
        win_y = y + 1 + round(ry * (h - 2))
        win_w = round(rw * (w - 2))
        win_h = round(rh * (h - 2))

        draw_box_outline(stdscr, win_y, win_x, win_h, win_w)

        try:
            stdscr.addstr(win_y + 1, win_x + 1, window.app_id[:max(win_w - 2, 0)])
        except curses.error:
            pass


def nav_items(box, state) -> list[NavItem]:
    return []
