"""Preview module: shows windows of the currently focused workspace,
each drawn as a scaled-down box at its real relative position — which
means overlapping windows (tiled splits sharing an edge, or a floating
window sitting on top of a tiled one) commonly produce overlapping
boxes here too, not just an edge case.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_corner_marks, draw_filled_box, draw_centered_lines, centered_x, display_width, wc_truncate, wrap_text
from tuicc.title_condense import condense_title


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    showing_preview = ctx.selected_item is not None and ctx.selected_item.preview_text is not None

    # Unconditionally re-blank this box's whole inner area with real
    # space characters, every frame, before EITHER branch below draws
    # anything — found live, stdscr.erase() alone (called once per
    # frame in frame_update.py) does not reliably clear a cell that
    # previously held a wide/emoji character's continuation: isolated
    # repro confirmed a stray leftover glyph surviving into a LATER
    # frame that lands on the sparse workspace-tile branch below
    # (which only draws where an actual window exists, leaving gaps
    # that rely entirely on erase() having cleared them) — the
    # equivalent fix inside draw_centered_lines() only helps when THAT
    # function runs, which the workspace-tile branch never calls. See
    # CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption
    # for the fuller story and the two earlier fixes that didn't
    # reach this specific gap.
    draw_filled_box(stdscr, y + 1, x + 1, h - 2, w - 2, 0)

    # A preview marked urgent (NavItem.preview_urgent — sysmon.py's
    # diagnostics row when it has real issues) colors the whole border
    # urgent, taking priority over the plain active/selected styling
    # below — the border is what signals "pay attention here"
    # everywhere else in this codebase.
    if showing_preview and ctx.selected_item.preview_urgent:
        outer_color = theme.get("urgent", 0)
    elif is_active:
        outer_color = theme.get("border_selected", 0)
    else:
        outer_color = theme.get("border", 0)

    # Camera-viewfinder corner marks for the module's own outer box —
    # the opposite of _draw_window()'s own per-window boxes below
    # (which stay full outlines): this is the one box in the module
    # that never overlaps anything else, so there's no "competing
    # lines" problem to solve, just the look itself. arm=2, bigger than
    # _draw_window()'s implicit default — a large box reads better with
    # a proportionally longer corner arm than a small one would.
    draw_corner_marks(stdscr, y, x, h, w, outer_color, arm=2)

    if showing_preview:
        draw_centered_lines(stdscr, box, ctx.selected_item.preview_text)
        return

    target_id = ctx.focus_id if ctx.focus_id is not None else ctx.state.focused_region_id

    focused_region = None
    for region in ctx.state.regions:
        if region.id == target_id:
            focused_region = region

    if focused_region is None:
        return

    tiled = [win for win in focused_region.windows if not win.floating]
    floating = [win for win in focused_region.windows if win.floating]

    for window in tiled:
        is_selected = f"preview:{window.id}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        _draw_window(stdscr, window, x, y, w, h, border_color, theme.get("text", 0), ctx.config)

    for window in floating:
        is_selected = f"preview:{window.id}" == ctx.selected_id
        color = theme.get("selected", 0) if is_selected else theme.get("accent", 0)
        _draw_window(stdscr, window, x, y, w, h, color, color, ctx.config, filled=True)


def _window_label(window, cfg):
    """Pure logic: what to draw as a window's label — "[app_id] detail"
    (e.g. "[kitty] htop") when the condensed title adds something real
    beyond the app's own name, else plain unbracketed app_id. See
    title_condense.py's own module docstring for where that shared
    condensing logic lives. Truncated to fit the box's width by the
    caller.
    """
    detail = condense_title(window.app_id, window.title, cfg)
    if detail and detail.lower() != window.app_id.lower():
        return f"[{window.app_id}] {detail}"
    return window.app_id


def _corner_label(window):
    """Pure logic: the compact per-corner identifier — the app_id's
    own first letter, uppercased, in brackets ("[K]" for kitty, "[F]"
    for firefox, "[C]" for code). Small enough to survive even a tight
    overlap between two windows' corners, where the full label (see
    _window_label(), drawn once, centered) would get lost entirely.
    Falls back to "[?]" for the pathological case of an app_id with no
    characters at all — a real WM shouldn't ever report that, but
    label drawing shouldn't crash if one somehow did.
    """
    app_id = window.app_id or ""
    letter = app_id[0].upper() if app_id else "?"
    return f"[{letter}]"


def _corner_positions(win_y, win_x, win_h, win_w, label_len):
    """Pure logic: the four (row, col) positions to draw a label at,
    one per corner of a box, inset by one cell from the border, with
    the right-hand corners right-aligning the label's end against that
    inset. Repeating the same label in all four corners (not just
    top-left) is deliberate: preview boxes commonly overlap, and an
    overlapping window's border can hide whichever single corner a
    label would otherwise be confined to. Degenerate/tiny boxes can
    produce duplicate or off-box positions — harmless, the caller's own
    curses.error guard around each addstr already handles it.
    """
    top = win_y + 1
    bottom = win_y + win_h - 2
    left = win_x + 1
    right = win_x + win_w - 1 - label_len
    return [(top, left), (top, right), (bottom, left), (bottom, right)]


def _draw_window(stdscr, window, x, y, w, h, border_color, text_color, cfg, filled=False):
    rx, ry, rw, rh = window.rect

    win_x = x + 1 + round(rx * (w - 2))
    win_y = y + 1 + round(ry * (h - 2))
    win_w = round(rw * (w - 2))
    win_h = round(rh * (h - 2))

    if filled:
        draw_filled_box(stdscr, win_y, win_x, win_h, win_w, border_color)

    draw_box_outline(stdscr, win_y, win_x, win_h, win_w, border_color)

    # Corner labels (dimmed — identifying detail, not something that
    # should visually compete with the window's own border/selection
    # color the way full-brightness text would) survive tight overlap
    # between windows; the full label below, shown once in the center,
    # is where the real detail (what's actually running) lives.
    corner_label = wc_truncate(_corner_label(window), max(win_w - 2, 0))
    for row, col in _corner_positions(win_y, win_x, win_h, win_w, display_width(corner_label)):
        try:
            stdscr.addstr(row, col, corner_label, text_color | curses.A_DIM)
        except curses.error:
            pass

    # window.title (via _window_label -> condense_title) is real,
    # uncontrolled window-title text — a browser tab, a document name —
    # genuinely can contain wide/CJK characters or emoji, the exact
    # class of content this whole codebase's width-awareness pass
    # exists for. Word-wrapped (wrap_text(), render_utils.py) across as
    # many centered lines as the window box has room for, rather than
    # clipped to one — a maximized/near-full-box window usually has
    # real room to spare, and a title read once then left static
    # doesn't need marquee_text()'s continuous-motion treatment the way
    # media.py's now-playing text does (that's the right call for
    # sidebar.py's own cramped per-window row, just not here). Lines
    # past however many fit just don't draw, same graceful-clip
    # tolerance wc_truncate's single-line version already had.
    inner_w = max(win_w - 2, 0)
    label_lines = wrap_text(_window_label(window, cfg), inner_w)[:max(win_h - 2, 0)]
    start_row = win_y + max((win_h - len(label_lines)) // 2, 0)
    for i, line in enumerate(label_lines):
        try:
            stdscr.addstr(start_row + i, centered_x(win_x + 1, inner_w, line), line, text_color)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box

    target_id = ctx.focus_id if ctx.focus_id is not None else ctx.state.focused_region_id

    focused_region = None
    for region in ctx.state.regions:
        if region.id == target_id:
            focused_region = region

    if focused_region is None:
        return []

    items = []
    for window in focused_region.windows:
        rx, ry, rw, rh = window.rect

        win_x = x + 1 + round(rx * (w - 2))
        win_y = y + 1 + round(ry * (h - 2))
        win_w = round(rw * (w - 2))
        win_h = round(rh * (h - 2))

        items.append(NavItem(
            id=f"preview:{window.id}",
            rect=(win_x, win_y, win_w, win_h),
            focus_target=window.id,
            target_kind="window",
        ))

    items.sort(key=lambda item: item.rect[0])

    return items
