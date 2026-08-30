"""Compact sidebar: same workspace-picker role as sidebar.py, sized for
a layout with no room to spare — one row per workspace slot (plus a
blank spacer row after each, since there's usually plenty of vertical
room for only total_workspaces slots), number and an occupied/empty
dot, no window listing. Meant for setups where preview is the star
(e.g. a preview-only layout with no room for sidebar's full window
details) but you still want a way to switch which workspace it's
showing.

Deliberately its own module, not a "compact mode" flag on sidebar.py —
per this codebase's module contract, each module owns its draw() and
nav_items() outright; a shared flag would mean sidebar.py's rendering
code has to branch on it internally, coupling the two instead of
letting them vary independently.

Items are target_kind="region" (NavItem's default) — the exact same
kind sidebar.py's items are, so selecting one flows through the
existing generic region-selection machinery (resolve_selection sets
focus_id, preview's live-follow-until-explicit-pick behavior) with no
new plumbing: whichever module happens to produce a region item works
interchangeably with any other, by construction.

---
IMPORTANT: Each module owns both how it draws itself and where its own
focusable items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, centered_x
from tuicc.modules.sidebar import slot_ids

ROW_STEP = 2  # 1 content row + 1 blank spacer row


def _build_slots(ctx):
    """See sidebar.py's slot_ids() docstring — same real-workspace-name
    logic (GitHub issue #9), reused rather than duplicated so the two
    sidebar variants can never drift apart on which slots exist.
    """
    by_id = {region.id: region for region in ctx.state.regions}
    ids = slot_ids(
        ctx.state.regions, ctx.wm_config, ctx.config.total_workspaces,
        ctx.config.workspace_mode, ctx.config.workspace_names,
    )
    return [(ws_id, by_id.get(ws_id)) for ws_id in ids]


def _slot_positions(box, ctx):
    """(ws_id, region, row) for every slot that fits in box — the exact
    same row math draw() and nav_items() both need, factored out once
    so the two can never drift out of sync (nav_items' rect has to
    land on precisely the row draw() painted the selection onto).
    """
    x, y, w, h = box
    positions = []
    row = y + 1
    for ws_id, region in _build_slots(ctx):
        if row >= y + h - 1:
            break
        positions.append((ws_id, region, row))
        row += ROW_STEP
    return positions


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="WS")

    inner_w = max(w - 2, 0)
    for ws_id, region, row in _slot_positions(box, ctx):
        is_selected = f"sidebar_compact:{ws_id}" == ctx.selected_id
        occupied = region is not None and len(region.windows) > 0
        dot = "●" if occupied else "○"
        dot_color = theme.get("accent", 0) if occupied else (theme.get("text", 0) | curses.A_DIM)
        num_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)

        content = f"{dot} {ws_id}"
        cx = centered_x(x + 1, inner_w, content)

        try:
            stdscr.addstr(row, cx, dot, dot_color)
            stdscr.addstr(row, cx + 2, ws_id, num_color | curses.A_BOLD)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    return [
        NavItem(
            id=f"sidebar_compact:{ws_id}",
            rect=(x + 1, row, max(w - 2, 0), 1),
            focus_target=ws_id,
        )
        for ws_id, region, row in _slot_positions(box, ctx)
    ]