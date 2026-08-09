"""Preview module: shows windows of the currently focused workspace.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_filled_box, draw_centered_lines


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    showing_preview = ctx.selected_item is not None and ctx.selected_item.preview_text is not None

    # A preview marked urgent (NavItem.preview_urgent — sysmon.py's
    # diagnostics row when it has real issues, not "all clear") colors
    # the WHOLE border urgent, taking priority over the plain active/
    # selected styling below — found live, asked for: the border is
    # what signals "pay attention here" everywhere else in this
    # codebase, so a preview showing a real problem should color that
    # border too, not just the text inside it.
    if showing_preview and ctx.selected_item.preview_urgent:
        outer_color = theme.get("urgent", 0)
    elif is_active:
        outer_color = theme.get("border_selected", 0)
    else:
        outer_color = theme.get("border", 0)

    draw_box_outline(stdscr, y, x, h, w, outer_color)

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
        _draw_window(stdscr, window, x, y, w, h, border_color, theme.get("text", 0))

    for window in floating:
        is_selected = f"preview:{window.id}" == ctx.selected_id
        color = theme.get("selected", 0) if is_selected else theme.get("accent", 0)
        _draw_window(stdscr, window, x, y, w, h, color, color, filled=True)


def _draw_window(stdscr, window, x, y, w, h, border_color, text_color, filled=False):
    rx, ry, rw, rh = window.rect

    win_x = x + 1 + round(rx * (w - 2))
    win_y = y + 1 + round(ry * (h - 2))
    win_w = round(rw * (w - 2))
    win_h = round(rh * (h - 2))

    if filled:
        draw_filled_box(stdscr, win_y, win_x, win_h, win_w, border_color)

    draw_box_outline(stdscr, win_y, win_x, win_h, win_w, border_color)

    try:
        stdscr.addstr(win_y + 1, win_x + 1, window.app_id[:max(win_w - 2, 0)], text_color)
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
