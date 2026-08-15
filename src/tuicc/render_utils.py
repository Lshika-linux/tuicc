"""Shared curses drawing helpers used by multiple modules.

---
IMPORTANT: Only genuinely shared, low-level drawing primitives belong
here (e.g. box outlines). Module-specific drawing logic belongs in
the module's own file in modules/, not here.
"""

import curses

from wcwidth import wcswidth


def display_width(text: str) -> int:
    """Real terminal column width of text — NOT len(text), which counts
    Unicode codepoints, not rendered columns. The two only diverge once
    a string can contain a genuinely wide character (an emoji, a CJK
    character), but when it does, len() silently breaks every
    centering/clipping calculation downstream — and found live, it's
    worse than "looks a bit off": curses' own refresh() diffs against
    its internal model of what's already on screen, so a wrongly-
    positioned/wrongly-clipped write based on a bad width count
    reproduces the SAME wrong result every single frame, reading as
    permanent visual corruption, not a one-off glitch (see
    CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption).

    Uses wcwidth's wcswidth() (the POSIX primitive, matching what a
    real terminal's own column math is based on) rather than Python's
    stdlib unicodedata.east_asian_width() — that's a known-unreliable
    proxy specifically for emoji+variation-selector sequences, exactly
    the case this exists for. wcswidth() genuinely needs the WHOLE
    string, not a naive per-character sum: confirmed live, wcwidth('☀')
    reports 1 and wcwidth('️') reports 0, summing to 1 — but
    wcswidth('☀️') correctly reports 2, the real rendered width,
    because it understands the variation-selector SEQUENCE as a unit.

    Returns 0 (never a negative) for a string wcswidth() can't measure
    (embedded control characters) — same "floor at 0, never guess"
    discipline centered_x() already had before this existed.
    """
    width = wcswidth(text)
    return width if width is not None and width >= 0 else 0


def wc_truncate(text: str, max_width: int) -> str:
    """text, cut down to fit within max_width real terminal columns —
    the width-aware equivalent of text[:n]. A base character
    immediately followed by a variation selector (U+FE0F) is kept or
    dropped as ONE unit (matching display_width()'s own whole-sequence
    measurement, see its docstring) — never split so the base character
    survives without its selector, which would silently change which
    presentation form renders.
    """
    if max_width <= 0:
        return ""
    result = []
    used = 0
    i = 0
    while i < len(text):
        unit = text[i:i + 2] if i + 1 < len(text) and text[i + 1] == "️" else text[i]
        unit_width = display_width(unit)
        if used + unit_width > max_width:
            break
        result.append(unit)
        used += unit_width
        i += len(unit)
    return "".join(result)


def draw_width_safe(stdscr, row, col, text, color):
    """Draws text starting at (row, col) — but any character wider
    than 1 column (an emoji, a VS16 sequence) gets its OWN isolated
    addstr() call, with every subsequent segment's start column
    computed explicitly via display_width(), never inherited from
    wherever curses itself left the cursor after writing the wide
    glyph.

    Exists because a single addstr() call spanning a wide character
    isn't safe on its own even once display_width()'s calculation is
    correct: confirmed live, ncurses' own internal C-level cursor-
    advance bookkeeping for a wide/VS16 glyph can disagree with what
    the real terminal (kitty) actually renders it as — and that
    mismatch happens one layer below anything a Python-side width
    calculation can reach or correct (see
    CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption).
    Every draw call in this codebase already explicitly specifies
    (row, col) rather than relying on cursor continuity ACROSS calls —
    this extends the same discipline to WITHIN one line: isolating each
    wide glyph into its own call, with its neighbors' positions
    computed independently, means no write's correctness ever depends
    on curses' own (possibly wrong) idea of how far the previous write
    advanced the cursor.
    """
    x = col
    run_start = 0
    i = 0
    while i < len(text):
        unit_len = 2 if i + 1 < len(text) and text[i + 1] == "️" else 1
        unit = text[i:i + unit_len]
        if display_width(unit) > 1:
            if i > run_start:
                run = text[run_start:i]
                try:
                    stdscr.addstr(row, x, run, color)
                except curses.error:
                    pass
                x += display_width(run)
            try:
                stdscr.addstr(row, x, unit, color)
            except curses.error:
                pass
            x += display_width(unit)
            run_start = i + unit_len
        i += unit_len
    if run_start < len(text):
        run = text[run_start:]
        try:
            stdscr.addstr(row, x, run, color)
        except curses.error:
            pass


def draw_box_outline(stdscr, y, x, h, w, color_pair=0, title=None):
    if h < 1 or w < 1:
        return

    try:
        if title:
            label = f" {title} "
            available = max(w - 2, 0)
            if display_width(label) >= available:
                top = "┌" + "─" * (w - 2) + "┐"
            else:
                left_dashes = 1
                right_dashes = available - display_width(label) - left_dashes
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


def draw_table_box(stdscr, y, x, h, w, title, columns, header_color=0, value_color=0, border_color=0):
    """A bordered box (draw_box_outline, with `title` cut into the top
    border) holding one header row + one value row, column-aligned —
    for a single-row "info panel" table (modules/connectivity.py's own
    WiFi device info is the first consumer, modeled on impala's own
    bordered "Device" panel rather than plain stacked preview_text
    lines). `columns` is [(label, value), ...]; each column's own
    width is max(len(label), len(value)), columns separated by a
    fixed gap, the whole two-row block centered as one unit within the
    box. Degrades gracefully (wc_truncate) rather than overflowing if
    it doesn't fit — same tolerance every other primitive here has.

    Padding uses plain str.ljust (character count, not display_width)
    deliberately, unlike wc_truncate elsewhere in this codebase: every
    realistic column here (interface name, MAC address, "station",
    vendor string) is effectively ASCII, not user-typed/uncontrolled
    text the way an SSID or window title is — the extra wide-character-
    aware padding machinery those need would be unexercised complexity
    here.
    """
    draw_box_outline(stdscr, y, x, h, w, border_color, title=title)
    if h < 3 or w < 3 or not columns:
        return
    inner_w = max(w - 2, 0)
    gap = "   "
    col_widths = [max(len(label), len(value)) for label, value in columns]
    header_line = gap.join(label.ljust(width) for (label, _value), width in zip(columns, col_widths)).rstrip()
    value_line = gap.join(value.ljust(width) for (_label, value), width in zip(columns, col_widths)).rstrip()
    header_line = wc_truncate(header_line, inner_w)
    value_line = wc_truncate(value_line, inner_w)
    try:
        stdscr.addstr(y + 1, centered_x(x + 1, inner_w, header_line), header_line, header_color | curses.A_BOLD)
        stdscr.addstr(y + 2, centered_x(x + 1, inner_w, value_line), value_line, value_color)
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
    padding = max(box_w - display_width(text), 0)
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

    # Explicitly blank the whole inner area with plain spaces before
    # drawing this frame's content — found live, this box gets reused
    # across frames for wildly different content (whichever NavItem is
    # currently selected), and stdscr.erase() alone was NOT enough to
    # reliably clear it once a previous frame's content included a wide
    # (emoji) character: isolated live repro confirmed the very next
    # frame after a wide-character line, on totally different (all-
    # ASCII) content, still showed stray leftover glyphs at the same
    # position. Root cause not fully understood at the ncurses-internals
    # level (see CLAUDE/NOTES/design-decisions.md
    # #rwb-wide-character-corruption for the two prior fixes that did
    # NOT solve this on their own) — this sidesteps it empirically by
    # never trusting erase()'s own diffing for this specific box, always
    # force-writing real space characters here first.
    draw_filled_box(stdscr, y + 1, x + 1, max_rows, inner_w, 0)

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
    for i, (text, color) in enumerate(lines):
        row = start_row + i
        if row < top_row or row >= top_row + max_rows:
            continue
        clipped = wc_truncate(text, col_w)
        col = centered_x(col_x, col_w, clipped) if center_each_line else col_x
        draw_width_safe(stdscr, row, col, clipped, color)


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
            stdscr.addstr(row, x + 2, wc_truncate(text, inner_w), color)
    except curses.error:
        pass


def draw_status_line(stdscr, term_width, text, color_pair):
    """A single line at the top-left of the screen — main.py's shared
    mechanism for a transient hint or toast (a resize-mode hint, a
    spawn-picker choice list, a "saved as preset N" message). Clipped
    to term_width so it fits rather than raising curses.error.
    """
    try:
        stdscr.addstr(0, 0, wc_truncate(text, term_width), color_pair)
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


MARQUEE_STEP_SECONDS = 0.3


def marquee_text(text: str, width: int, now: float) -> str:
    """text as-is if it already fits width (measured in terminal
    columns, see display_width() above — a CJK title can be well over
    `width` columns wide despite having few enough characters to pass a
    plain len() check). Otherwise a width-wide sliding window into
    "text + gap" repeated, advancing one character every
    MARQUEE_STEP_SECONDS of wall-clock time — same "no per-frame state
    needed, time.time() already is the shared clock" reasoning
    _pending_blink_style (modules/*.py) uses, just taking `now` as an
    explicit parameter instead of calling time.time() internally, so
    this stays unit-testable without a real clock (callers pass
    time.time() in).

    Originally modules/media.py's own (Now Playing's artist/title
    scroll); moved here once modules/sidebar.py needed the identical
    behavior for an overflowing window title — same "shared, low-level
    drawing primitive" bar eighth_block_level() already cleared, see
    that function's own docstring. No wrapper needed on either caller's
    side: unlike _cava_row_level's own thin wrapper over
    eighth_block_level (which bakes in a domain-specific max value),
    this signature is already fully general.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    gap = "   "
    looped = text + gap
    offset = int(now / MARQUEE_STEP_SECONDS) % len(looped)
    doubled = looped + looped  # a character-index slice never runs off the end
    return wc_truncate(doubled[offset:], width)


def wrap_text(text: str, width: int) -> list[str]:
    """Word-wrap text into width-wide (display-width aware) lines —
    the multi-line counterpart to wc_truncate's single-line clip.
    Built for modules/preview.py's per-window label: a maximized
    window's own box is usually wide enough that word-wrapping a long
    title across a couple of centered lines reads better than either a
    single truncated line or a scrolling marquee (a title read once,
    then left static, doesn't need marquee_text()'s continuous-motion
    treatment the way media.py's now-playing text does — that's still
    the right call for sidebar.py's cramped per-window row, just not
    here).

    A single word wider than `width` on its own (a long URL-shaped
    title with no spaces) is hard-split via wc_truncate rather than
    left overflowing the box — same "never write past the caller's
    stated bound" contract wc_truncate itself keeps. Always returns at
    least one (possibly empty) line, so callers can index [0] safely.
    """
    if width <= 0:
        return [""]
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if display_width(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while display_width(word) > width:
            chunk = wc_truncate(word, width)
            if not chunk:
                # width is too narrow to fit even this word's first
                # character (e.g. a lone CJK character against a
                # 1-column budget) — stop hard-splitting rather than
                # looping forever on a word that never shrinks.
                break
            lines.append(chunk)
            word = word[len(chunk):]
        current = word
    if current or not lines:
        lines.append(current)
    return lines


def format_shortcut(key_name: str) -> str:
    """Turn a keybinds.py-style key spec into display text, e.g.
    "Ctrl+L" -> "[^L]". Shared so any module showing a keybind hint
    uses the same convention instead of inventing its own.
    """
    if key_name.startswith("Ctrl+"):
        return f"[^{key_name[len('Ctrl+'):].upper()}]"
    return f"[{key_name}]"
