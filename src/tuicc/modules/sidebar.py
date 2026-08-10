"""Sidebar module: always shows total_workspaces slots, populated from
whichever regions currently exist. Occupied slots grow to list every
window inside them; empty slots stay a single thin row.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline
from tuicc.title_condense import condense_title


def _slot_height(region, preview_count=0):
    base = 2 if region is None else 2 + len(region.windows)
    return base + preview_count


def _preview_apps_for(ctx, ws_id):
    """The incoming app_ids a saved-but-not-yet-loaded session would
    spawn onto this workspace, if the Sessions module currently has a
    slot expanded and that slot has something saved — see
    RenderContext.session_preview's own docstring. [] the overwhelming
    rest of the time (nothing expanded, or this workspace isn't one of
    that session's targets), so callers never need their own None check.
    """
    if not ctx.session_preview:
        return []
    return ctx.session_preview.get(ws_id, [])


def _build_slots(ctx):
    """Return a list of (workspace_number_str, region_or_None) for every
    slot from 1 to total_workspaces, filled in with real regions where
    they exist.
    """
    by_id = {region.id: region for region in ctx.state.regions}
    slots = []
    for n in range(1, ctx.config.total_workspaces + 1):
        slots.append((str(n), by_id.get(str(n))))
    return slots


def shift_workspace_id(current_id, total_workspaces, delta):
    """current_id shifted by delta, wrapping within 1..total_workspaces
    — used by main.py's "launcher" input_claim tier to let Up/Down move
    the ambient-typing launch target (ctx.focus_id) while still typing,
    without leaving typing mode (Left/Right are already spoken for —
    they move which search RESULT is selected — and arrow keys never
    collide with typed characters, since they're outside the printable
    range handle_typing_key checks). current_id may be None (nothing
    explicitly selected yet) or non-numeric (this codebase's sidebar
    only ever shows numbered 1..total_workspaces slots to begin with,
    see _build_slots above — a provider surfacing named workspaces
    already doesn't fit this sidebar model) — falls back to slot 1
    rather than raising, since this is UI convenience, not something
    that should ever crash the render loop.
    """
    if total_workspaces <= 0:
        return current_id
    current = int(current_id) if current_id and current_id.isdigit() else 1
    new_n = ((current - 1 + delta) % total_workspaces) + 1
    return str(new_n)


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Workspaces")

    item_y = y + 1
    for ws_id, region in _build_slots(ctx):
        preview_apps = _preview_apps_for(ctx, ws_id)
        item_h = _slot_height(region, len(preview_apps))
        is_selected = f"sidebar:{ws_id}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        text_color = theme.get("text", 0)
        urgent_color = theme.get("urgent", 0)

        draw_box_outline(stdscr, item_y, x + 1, item_h, w - 2, border_color)

        # Found live, asked for directly: launching an app from
        # ANYWHERE (ambient typing — see VISION.md's own "start typing
        # from anywhere" identity commitment) always targets ctx.focus_id,
        # regardless of which module you were actually browsing when you
        # started typing — which workspace that actually IS wasn't
        # visible anywhere while typing. Label the one slot it's
        # actually going to land on, live, for as long as typing_mode
        # stays true.
        is_launch_target = ctx.typing_mode and ctx.focus_id == ws_id
        if is_launch_target:
            label = f" {ws_id} - launching here "
            label_color = theme.get("accent", 0) | curses.A_BOLD
        else:
            label = f" {ws_id} "
            label_color = text_color
        try:
            stdscr.addstr(item_y, x + 2, label[:max(w - 4, 0)], label_color)
        except curses.error:
            pass

        existing_count = 0
        if region is not None:
            for i, window in enumerate(region.windows):
                app = window.app_id
                detail = condense_title(app, window.title, ctx.config)
                available = max(w - 4, 0)

                try:
                    chunk = app[:available]
                    stdscr.addstr(item_y + 1 + i, x + 2, chunk, text_color | curses.A_BOLD)
                    cx = x + 2 + len(chunk)
                    end = x + 2 + available
                    if detail and cx + 1 < end:
                        stdscr.addstr(item_y + 1 + i, cx, f" {detail}"[:end - cx], text_color | curses.A_DIM)
                except curses.error:
                    pass
            existing_count = len(region.windows)

        # Apps a currently-expanded (see sessions.py) session slot would
        # spawn HERE if loaded — not yet real, so urgent (same role
        # power_menu uses for its destructive actions) instead of the
        # plain/bold style real windows get, and always listed after
        # them rather than interleaved.
        for i, app in enumerate(preview_apps):
            try:
                stdscr.addstr(item_y + 1 + existing_count + i, x + 2, app[:max(w - 4, 0)], urgent_color)
            except curses.error:
                pass

        item_y += item_h


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    items = []

    item_y = y + 1
    for ws_id, region in _build_slots(ctx):
        # Must match draw()'s own height exactly (including any preview
        # apps) — the item's rect is what's actually clickable/
        # highlighted, and item_y here has to track the same running
        # offset draw() uses for every slot after this one.
        item_h = _slot_height(region, len(_preview_apps_for(ctx, ws_id)))
        items.append(NavItem(
            id=f"sidebar:{ws_id}",
            rect=(x + 1, item_y, w - 2, item_h),
            focus_target=ws_id,
        ))
        item_y += item_h

    return items
