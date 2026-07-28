"""Clock module: shows the current time and date. No navigable items."""

import curses
from datetime import datetime

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, centered_x


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color)

    now = datetime.now()
    time_str = now.strftime(ctx.config.clock_time_format)
    date_str = now.strftime(ctx.config.clock_date_format)

    inner_w = w - 2
    time_x = x + 1 + centered_x(0, inner_w, time_str)
    date_x = x + 1 + centered_x(0, inner_w, date_str)

    try:
        stdscr.addstr(y + 1, time_x, time_str, theme.get("accent", 0))
        stdscr.addstr(y + 2, date_x, date_str, theme.get("text", 0))
    except curses.error:
        pass

def nav_items(box, ctx, module_name) -> list[NavItem]:
    return []
