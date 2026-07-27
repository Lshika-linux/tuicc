"""Sidebar module: lists workspaces (regions) and reports their nav items.

---
IMPORTANT:Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""
import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


def draw(stdscr, box, state, selected_id=None, focus_id=None):
    x, y, w, h = box

    draw_box_outline(stdscr, y, x, h, w)

    for i, region in enumerate(state.regions):
        label = f"[{region.id}] {region.name}"
        prefix = "> " if f"sidebar:{region.id}" == selected_id else "  "
        label = prefix + label
        try:
            stdscr.addstr(y + 1 + i, x + 1, label[:max(w - 2, 0)])
        except curses.error:
            pass

def nav_items(box, state) -> list[NavItem]:
    x, y, w, h = box
    items = []
    for i, region in enumerate(state.regions):
        items.append(NavItem(
            id=f"sidebar:{region.id}",
            rect=(x, y + 1 + i, w, 1),
            focus_target=region.id,
        ))
    return items
