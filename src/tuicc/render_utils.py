"""Shared curses drawing helpers used by multiple modules.

---
IMPORTANT: Only genuinely shared, low-level drawing primitives belong
here (e.g. box outlines). Module-specific drawing logic belongs in
the module's own file in modules/, not here.
"""

import curses


def draw_box_outline(stdscr, y, x, h, w):
    if h < 1 or w < 1:
        return

    try:
        stdscr.addstr(y, x, "+" + "-" * (w - 2) + "+")
        for i in range(1, h - 1):
            stdscr.addstr(y + i, x, "|")
            stdscr.addstr(y + i, x + w - 1, "|")
        stdscr.addstr(y + h - 1, x, "+" + "-" * (w - 2) + "+")
    except curses.error:
        pass
