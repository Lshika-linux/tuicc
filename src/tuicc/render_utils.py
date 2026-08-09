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


def draw_centered_lines(stdscr, box, lines):
    """A block of (text, color_pair) lines, centered both horizontally
    and vertically within box. Used for anything a module wants to
    show in place of its normal contents — a Y/N confirmation overlay,
    a preview of what an item would do — centered the same way
    everywhere instead of each caller inventing its own positioning.
    """
    x, y, w, h = box
    inner_w = max(w - 2, 0)
    start_row = y + max((h - len(lines)) // 2, 0)

    try:
        for i, (text, color) in enumerate(lines):
            row = start_row + i
            if row < y or row >= y + h - 1:
                continue
            clipped = text[:inner_w]
            col = centered_x(x + 1, inner_w, clipped)
            stdscr.addstr(row, col, clipped, color)
    except curses.error:
        pass


def draw_text_panel(stdscr, box, lines, border_color, title=None):
    """A bordered panel of left-aligned text lines. Unlike
    draw_centered_lines (built for a short confirm dialog, each line
    individually centered), this is for paragraph-shaped content: a
    consistent left margin, one line per row, no per-line centering.

    lines is a list of (text, color_pair) pairs — the caller decides
    each line's color (e.g. to highlight the currently-selected row in
    a list), this function only lays them out. Takes an explicit box
    like every other primitive here (draw_box_outline, draw_filled_box,
    draw_centered_lines) — a caller wanting "most of the screen, with a
    margin" computes that box itself, so this can also be used for a
    sub-region (e.g. one pane of a two-pane page).
    """
    x, y, w, h = box

    draw_box_outline(stdscr, y, x, h, w, border_color, title=title)

    inner_w = max(w - 4, 0)
    try:
        for i, (text, color) in enumerate(lines):
            row = y + 1 + i
            if row >= y + h - 1:
                break
            stdscr.addstr(row, x + 2, text[:inner_w], color)
    except curses.error:
        pass


def draw_status_line(stdscr, term_width, text, color_pair):
    """A single line at the top-left of the screen — main.py's shared
    mechanism for a transient hint or toast (a resize-mode hint, a
    spawn-picker choice list, a "saved as preset N" message). Clipped
    to term_width so it fits rather than raising curses.error.
    """
    try:
        stdscr.addstr(0, 0, text[:term_width], color_pair)
    except curses.error:
        pass


def eighth_block_level(value: int, max_value: int, row_idx: int, num_rows: int) -> int:
    """value's height (0..max_value) as seen from just ONE physical output
    row's own 0..8 slice, when the value is rendered as num_rows stacked
    terminal rows, each with 8 sub-levels of vertical resolution via the
    eighth-block glyphs (" ▁▂▃▄▅▆▇█"). Without this, a value only has
    num_rows discrete visual steps (one per whole terminal cell) — moving
    between two of them looks like a jump, not continuous motion, exactly
    the "bars don't move consistently with the real percentage" problem
    swcc's own vertical bars had. Scaling to num_rows*8 steps instead
    fixes that at any bar height, including num_rows=1.

    row_idx=0 is the TOPMOST row; row_idx=num_rows-1 is the BOTTOMMOST
    (where a bar visually starts growing from). Pure function — no
    curses, no state — shared by modules/media.py's cava visualizer
    (this is where the math originally lived, as `_cava_row_level`,
    before the bars module needed the exact same technique for its own
    VOL/BRI/BAT fills) and modules/bars.py.
    """
    if num_rows <= 0 or max_value <= 0:
        return 0
    total_levels = num_rows * 8
    scaled = value * total_levels // max_value
    scaled = max(0, min(scaled, total_levels))
    row_base = (num_rows - 1 - row_idx) * 8
    return max(0, min(scaled - row_base, 8))


def format_shortcut(key_name: str) -> str:
    """Turn a keybinds.py-style key spec into display text, e.g.
    "Ctrl+L" -> "[^L]". Shared so any module showing a keybind hint
    uses the same convention instead of inventing its own.
    """
    if key_name.startswith("Ctrl+"):
        return f"[^{key_name[len('Ctrl+'):].upper()}]"
    return f"[{key_name}]"
