"""Launcher module: placeholder — will host app launching and saved
workspace layouts (save/overwrite/run). No logic yet.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, centered_x


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color)

    label = "Launcher"
    label_x = x + 1 + centered_x(0, max(w - 2, 0), label)
    label_y = y + h // 2
    try:
        stdscr.addstr(label_y, label_x, label[:max(w - 2, 0)], theme.get("text", 0))
    except curses.error:
        pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    return []
