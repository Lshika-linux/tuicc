"""Shared curses drawing helpers used by multiple modules.

---
IMPORTANT: Only genuinely shared, low-level drawing primitives belong
here (e.g. box outlines). Module-specific drawing logic belongs in
the module's own file in modules/, not here.
"""

import curses


def draw_box_outline(stdscr, y, x, h, w, color_pair=0):
    if h < 1 or w < 1:
        return

    try:
        stdscr.addstr(y, x, "+" + "-" * (w - 2) + "+", color_pair)
        for i in range(1, h - 1):
            stdscr.addstr(y + i, x, "|", color_pair)
            stdscr.addstr(y + i, x + w - 1, "|", color_pair)
        stdscr.addstr(y + h - 1, x, "+" + "-" * (w - 2) + "+", color_pair)
    except curses.error:
        pass

def draw_filled_box(stdscr, y, x, h, w, color_pair=0):
    if h < 1 or w < 1:
        return

    try:
        for i in range(h):
            stdscr.addstr(y + i, x, " " * w, color_pair)
    except curses.error:
        pass
