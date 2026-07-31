"""Shared curses drawing helpers used by multiple modules.

---
IMPORTANT: Only genuinely shared, low-level drawing primitives belong
here (e.g. box outlines). Module-specific drawing logic belongs in
the module's own file in modules/, not here.
"""

import curses


def draw_box_outline(stdscr, y, x, h, w, color_pair=0, title=None):
    if h < 1 or w < 1:
        return

    try:
        if title:
            label = f" {title} "
            available = max(w - 2, 0)
            if len(label) >= available:
                top = "┌" + "─" * (w - 2) + "┐"
            else:
                left_dashes = 1
                right_dashes = available - len(label) - left_dashes
                top = "┌" + "─" * left_dashes + label + "─" * right_dashes + "┐"
            stdscr.addstr(y, x, top, color_pair)
        else:
            stdscr.addstr(y, x, "┌" + "─" * (w - 2) + "┐", color_pair)

        for i in range(1, h - 1):
            stdscr.addstr(y + i, x, "│", color_pair)
            stdscr.addstr(y + i, x + w - 1, "│", color_pair)
        stdscr.addstr(y + h - 1, x, "└" + "─" * (w - 2) + "┘", color_pair)
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

def centered_x(box_x, box_w, text):
    padding = max(box_w - len(text), 0)
    return box_x + padding // 2


def format_shortcut(key_name: str) -> str:
    """Turn a keybinds.py-style key spec into display text, e.g.
    "Ctrl+L" -> "[^L]". Shared so any module showing a keybind hint
    uses the same convention instead of inventing its own.
    """
    if key_name.startswith("Ctrl+"):
        return f"[^{key_name[len('Ctrl+'):].upper()}]"
    return f"[{key_name}]"
