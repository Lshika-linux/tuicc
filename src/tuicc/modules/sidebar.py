"""Sidebar module: always shows total_workspaces slots, populated from
whichever regions currently exist. Occupied slots grow to list every
window inside them; empty slots stay a single thin row.

Scrollable, windowed around the current selection — see
_visible_slot_range()'s own docstring for why this needs its own
variable-height budget mechanism rather than reusing windowed_list.py's
existing window_start() (built for N items of uniform, fixed row
height; a workspace slot's own height depends on how many windows it
holds, genuinely different math). Same underlying discipline as
windowed_list.py's own peek-item mechanism (sysmon.py/media.py) though:
nothing persisted between frames, recomputed fresh every draw() call,
Tab past the visible edge reaches a "peek" NavItem for the next
off-screen slot, which becomes the new selection and pulls the window
along with it next frame.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, display_width, wc_truncate
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


def _selected_slot_index(slots, selected_id) -> int | None:
    """Which 0-indexed slot ctx.selected_id currently points at, or
    None if selection isn't on this module at all (tabbed elsewhere —
    same "window resets to the top" behavior sysmon.py's own
    _selected_window_index gives _visible_slot_range below, deliberate
    and consistent, not a gap: nothing case (ctx.selected_id belongs to
    a different module) needs a stale scroll position remembered
    across it).
    """
    for i, (ws_id, _region) in enumerate(slots):
        if f"sidebar:{ws_id}" == selected_id:
            return i
    return None


def _visible_slot_range(heights: list[int], selected_index: int | None, available_rows: int) -> tuple[int, int]:
    """(start, end) — the widest contiguous [start, end) range of slots
    that fits within available_rows total rows while keeping
    selected_index inside it. Budget-based, not slot-count-based:
    windowed_list.py's own window_start() assumes every item is exactly
    one row (true for sysmon/media's own lists), which doesn't hold
    here — an occupied workspace's own slot height depends on how many
    windows it has (see _slot_height()).

    Anchors selected_index as the LAST slot in the window first,
    extending backward while budget allows, THEN extends forward with
    any budget still left over — mirrors window_start()'s own bias
    exactly (a selection newly scrolled into view lands at the BOTTOM
    of the window, not the top; confirmed against its own docstring:
    "shifts by exactly enough to make it the last visible slot").
    Extending forward-then-backfilling-backward instead (tried first,
    live-caught by its own test suite) puts the selection at the TOP
    of a freshly-scrolled-to window instead — technically still keeps
    it visible, but disagrees with the one other windowing convention
    already established in this codebase, which is worse than either
    choice alone. No persisted scroll-offset state, recomputed fresh
    from heights/selected_index every call (see this module's own
    docstring).

    A single slot taller than available_rows on its own (an absurd
    number of windows piled onto one workspace) still returns a
    1-slot range that itself overflows the box — nested scrolling
    WITHIN one slot's own window list isn't solved here, an accepted
    limit for a case this extreme, not a silent failure (the slot's
    own content just clips at the box edge like anything else that
    overflows a curses box).
    """
    n = len(heights)
    if n == 0:
        return 0, 0
    if selected_index is None:
        selected_index = 0
    selected_index = max(0, min(selected_index, n - 1))

    start = selected_index
    end = selected_index + 1
    total = heights[selected_index]
    while start > 0 and total + heights[start - 1] <= available_rows:
        start -= 1
        total += heights[start]
    while end < n and total + heights[end] <= available_rows:
        total += heights[end]
        end += 1
    return start, end


def shift_workspace_id(current_id, total_workspaces, delta):
    """current_id shifted by delta, wrapping within 1..total_workspaces
    — used by main.py's mode_stack "launcher" tier to let Up/Down move
    the ambient-typing launch target while still typing, without
    leaving typing mode. current_id may be None or non-numeric — falls
    back to slot 1 rather than raising, since this is UI convenience,
    not something that should ever crash the render loop.
    """
    if total_workspaces <= 0:
        return current_id
    current = int(current_id) if current_id and current_id.isdigit() else 1
    new_n = ((current - 1 + delta) % total_workspaces) + 1
    return str(new_n)


def _slot_data(ctx):
    """(ws_id, region, preview_apps, height) for every slot — the one
    place draw()/nav_items() both build this from, so the two can never
    drift apart the way two independent _build_slots()+_slot_height()
    call sites once could (each recomputed preview_apps separately).
    """
    data = []
    for ws_id, region in _build_slots(ctx):
        preview_apps = _preview_apps_for(ctx, ws_id)
        data.append((ws_id, region, preview_apps, _slot_height(region, len(preview_apps))))
    return data


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Workspaces")

    slots = _slot_data(ctx)
    heights = [s[3] for s in slots]
    selected_index = _selected_slot_index([(s[0], s[1]) for s in slots], ctx.selected_id)
    start, end = _visible_slot_range(heights, selected_index, max(h - 2, 0))

    item_y = y + 1
    for ws_id, region, preview_apps, item_h in slots[start:end]:
        is_selected = f"sidebar:{ws_id}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        text_color = theme.get("text", 0)
        urgent_color = theme.get("urgent", 0)

        draw_box_outline(stdscr, item_y, x + 1, item_h, w - 2, border_color)

        # Launching an app from anywhere (ambient typing — see
        # CLAUDE/VISION.md's "start typing from anywhere" identity
        # commitment) always targets ctx.focus_id, regardless of which
        # module you were actually browsing when you started typing —
        # without this, which workspace that actually is wasn't visible
        # anywhere while typing. Label the one slot it's actually going
        # to land on, live, for as long as typing_mode stays true.
        is_launch_target = ctx.typing_mode and ctx.focus_id == ws_id
        if is_launch_target:
            label = f" {ws_id} - launching here "
            label_color = theme.get("accent", 0) | curses.A_BOLD
        else:
            label = f" {ws_id} "
            label_color = text_color
        try:
            stdscr.addstr(item_y, x + 2, wc_truncate(label, max(w - 4, 0)), label_color)
        except curses.error:
            pass

        existing_count = 0
        if region is not None:
            for i, window in enumerate(region.windows):
                app = window.app_id
                detail = condense_title(app, window.title, ctx.config)
                available = max(w - 4, 0)

                try:
                    chunk = wc_truncate(app, available)
                    stdscr.addstr(item_y + 1 + i, x + 2, chunk, text_color | curses.A_BOLD)
                    cx = x + 2 + display_width(chunk)
                    end = x + 2 + available
                    if detail and cx + 1 < end:
                        stdscr.addstr(item_y + 1 + i, cx, wc_truncate(f" {detail}", end - cx), text_color | curses.A_DIM)
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
                stdscr.addstr(item_y + 1 + existing_count + i, x + 2, wc_truncate(app, max(w - 4, 0)), urgent_color)
            except curses.error:
                pass

        item_y += item_h


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box

    slots = _slot_data(ctx)
    heights = [s[3] for s in slots]
    selected_index = _selected_slot_index([(s[0], s[1]) for s in slots], ctx.selected_id)
    start, end = _visible_slot_range(heights, selected_index, max(h - 2, 0))

    items = []
    item_y = y + 1
    rects = {}  # ws_id -> rect, so the peek items below can reuse the boundary slots' own
    for ws_id, _region, _preview_apps, item_h in slots[start:end]:
        # Must match draw()'s own height exactly (including any preview
        # apps) — the item's rect is what's actually clickable/
        # highlighted, and item_y here has to track the same running
        # offset draw() uses for every slot after this one.
        rect = (x + 1, item_y, w - 2, item_h)
        rects[ws_id] = rect
        items.append(NavItem(id=f"sidebar:{ws_id}", rect=rect, focus_target=ws_id))
        item_y += item_h

    # Peek items for the scrollable window, same mechanism sysmon.py/
    # media.py's own windowed_list.py-based sections use (see this
    # module's own docstring): a hidden slot just past either edge gets
    # a NavItem reusing the boundary VISIBLE slot's own rect, not drawn
    # as itself — Tab reaching it updates ctx.selected_id, and since
    # nothing here is cached, _visible_slot_range recomputes around the
    # new selection before the next frame ever draws it as
    # selected-but-invisible.
    if start > 0:
        before_ws_id = slots[start - 1][0]
        items.insert(0, NavItem(id=f"sidebar:{before_ws_id}", rect=rects[slots[start][0]], focus_target=before_ws_id))
    if end < len(slots):
        after_ws_id = slots[end][0]
        items.append(NavItem(id=f"sidebar:{after_ws_id}", rect=rects[slots[end - 1][0]], focus_target=after_ws_id))

    return items
