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


def draw_corner_marks(stdscr, y, x, h, w, color_pair=0, arm=1):
    """Open corner brackets — a short horizontal + vertical arm at each
    of a box's 4 corners — instead of draw_box_outline()'s full
    rectangle. Cuts visual clutter where full outlines would otherwise
    cross/compete (modules/preview.py's per-window boxes commonly
    overlap). arm is clamped so opposite corners never touch on a small
    box, degrading to a bare corner glyph (arm=0) rather than drawing
    something visually wrong.
    """
    if h < 1 or w < 1:
        return

    arm = max(0, min(arm, (w - 1) // 2, (h - 1) // 2))

    try:
        stdscr.addstr(y, x, "┌" + "─" * arm, color_pair)
        stdscr.addstr(y, x + w - 1 - arm, "─" * arm + "┐", color_pair)
        stdscr.addstr(y + h - 1, x, "└" + "─" * arm, color_pair)
        stdscr.addstr(y + h - 1, x + w - 1 - arm, "─" * arm + "┘", color_pair)
        for i in range(1, arm + 1):
            stdscr.addstr(y + i, x, "│", color_pair)
            stdscr.addstr(y + i, x + w - 1, "│", color_pair)
            stdscr.addstr(y + h - 1 - i, x, "│", color_pair)
            stdscr.addstr(y + h - 1 - i, x + w - 1, "│", color_pair)
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
    a preview of what an item would do. More lines than fit in one
    column spill into a second column side by side (see
    CLAUDE/VISION.md's R6 section for the overflow bug this fixes);
    genuinely too much even for two columns still truncates with a
    "+N more" marker.
    """
    x, y, w, h = box
    inner_w = max(w - 2, 0)
    max_rows = max(h - 2, 0)  # rows actually inside the border (y+1 .. y+h-2)
    if max_rows <= 0 or not lines:
        return

    if len(lines) <= max_rows:
        _draw_centered_column(stdscr, x + 1, y + 1, inner_w, max_rows, lines, center_each_line=True)
        return

    # Two side-by-side columns, each getting half the inner width
    # (minus a small gap between them) — left-aligned within its own
    # column rather than individually centered per line, which would
    # stagger unevenly against its neighbor; a shared left margin per
    # column reads as one coherent block instead.
    col_w = max((inner_w - 2) // 2, 1)
    left_lines, right_lines = split_lines_into_columns(lines, max_rows)
    _draw_centered_column(stdscr, x + 1, y + 1, col_w, max_rows, left_lines, center_each_line=False)
    _draw_centered_column(stdscr, x + 1 + col_w + 2, y + 1, col_w, max_rows, right_lines, center_each_line=False)


def split_lines_into_columns(lines: list, max_rows: int) -> tuple[list, list]:
    """Pure column-split math for draw_centered_lines' overflow case —
    separated out from the actual curses drawing so the truncation/
    "+N more" logic is unit-testable without a real curses screen
    (unlike draw_centered_lines itself, which needs one — see this
    file's own module docstring on why curses-drawing functions are
    otherwise left untested here).
    """
    if max_rows <= 0:
        return [], []
    capacity = max_rows * 2
    if len(lines) > capacity:
        shown = lines[:max(capacity - 1, 0)]
        fallback_color = shown[-1][1] if shown else 0
        shown = shown + [(f"+{len(lines) - len(shown)} more", fallback_color)]
    else:
        shown = lines
    return shown[:max_rows], shown[max_rows:]


def _draw_centered_column(stdscr, col_x, top_row, col_w, max_rows, lines, center_each_line):
    """One column's worth of lines, vertically centered within its own
    max_rows budget — shared by draw_centered_lines' single-column and
    two-column paths above.
    """
    start_row = top_row + max((max_rows - len(lines)) // 2, 0)
    try:
        for i, (text, color) in enumerate(lines):
            row = start_row + i
            if row < top_row or row >= top_row + max_rows:
                continue
            clipped = text[:col_w]
            col = centered_x(col_x, col_w, clipped) if center_each_line else col_x
            stdscr.addstr(row, col, clipped, color)
    except curses.error:
        pass


def draw_text_panel(stdscr, box, lines, border_color, title=None):
    """A bordered panel of left-aligned text lines. Unlike
    draw_centered_lines (built for a short confirm dialog, each line
    individually centered), this is for paragraph-shaped content: a
    consistent left margin, one line per row, no per-line centering.
    lines is (text, color_pair) pairs — the caller decides each line's
    color, this only lays them out. Takes an explicit box like every
    other primitive here, so it also works for a sub-region.
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
    """value's height (0..max_value) as seen from just ONE physical
    output row's 0..8 slice, when rendered as num_rows stacked terminal
    rows with 8 sub-levels each via eighth-block glyphs (" ▁▂▃▄▅▆▇█").
    Without this, a value only has num_rows discrete visual steps —
    scaling to num_rows*8 makes motion between them continuous, not a
    jump, at any bar height. row_idx=0 is topmost, row_idx=num_rows-1
    is bottommost. Pure function, shared by modules/media.py's cava
    visualizer (where this math originally lived) and modules/bars.py.
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
