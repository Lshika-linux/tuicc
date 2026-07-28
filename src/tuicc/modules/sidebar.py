"""Sidebar module: lists workspaces (regions) and reports their nav items.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


ITEM_HEIGHT = 3


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)

    draw_box_outline(stdscr, y, x, h, w, outer_color)

    for i, region in enumerate(ctx.state.regions):
        item_y = y + 1 + i * ITEM_HEIGHT
        is_selected = f"sidebar:{region.id}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)

        draw_box_outline(stdscr, item_y, x + 1, ITEM_HEIGHT, w - 2, border_color)

        label = f"[{region.id}] {region.name}"
        try:
            stdscr.addstr(item_y + 1, x + 2, label[:max(w - 4, 0)], text_color)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    items = []
    for i, region in enumerate(ctx.state.regions):
        item_y = y + 1 + i * ITEM_HEIGHT
        items.append(NavItem(
            id=f"sidebar:{region.id}",
            rect=(x + 1, item_y, w - 2, ITEM_HEIGHT),
            focus_target=region.id,
        ))
    return items
