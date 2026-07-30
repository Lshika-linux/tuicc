"""Sidebar module: always shows total_workspaces slots, populated from
whichever regions currently exist. Occupied slots grow to list every
window inside them; empty slots stay a single thin row.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses
import re

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


_TITLE_SPLIT_RE = re.compile(r"\s+[-—–|]\s+")


def _condense_title(app, title, cfg):
    """Condensed window title — only the part that adds information
    beyond the app's own name. Empty string = nothing useful to show.

    Which app_ids count as "terminal" or "browser" comes from
    [title_condense] in config.toml, not hardcoded here — the shape of
    the heuristic (terminals show the full title, browsers show just
    the site name, everything else shows its first segment) is generic
    across WMs and users, but *which apps* fall into which bucket is
    specific to whoever's config it is.
    """
    app_l = (app or "").lower()
    title = (title or "").strip()
    if not title:
        return ""

    if app_l in cfg.terminal_apps:
        return title

    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    if not parts:
        return title

    if app_l in cfg.browser_apps:
        segs = [p for p in parts if p.lower() not in cfg.browser_title_names] or parts
        return segs[-1]

    first = parts[0]
    if first.lower() == app_l:
        return ""
    return first


def _slot_height(region):
    if region is None:
        return 2
    return 2 + len(region.windows)


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


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Workspaces")

    item_y = y + 1
    for ws_id, region in _build_slots(ctx):
        item_h = _slot_height(region)
        is_selected = f"sidebar:{ws_id}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        text_color = theme.get("text", 0)

        draw_box_outline(stdscr, item_y, x + 1, item_h, w - 2, border_color)

        label = f" {ws_id} "
        try:
            stdscr.addstr(item_y, x + 2, label[:max(w - 4, 0)], text_color)
        except curses.error:
            pass

        if region is not None:
            for i, window in enumerate(region.windows):
                app = window.app_id
                detail = _condense_title(app, window.title, ctx.config)
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

        item_y += item_h


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    items = []

    item_y = y + 1
    for ws_id, region in _build_slots(ctx):
        item_h = _slot_height(region)
        items.append(NavItem(
            id=f"sidebar:{ws_id}",
            rect=(x + 1, item_y, w - 2, item_h),
            focus_target=ws_id,
        ))
        item_y += item_h

    return items
