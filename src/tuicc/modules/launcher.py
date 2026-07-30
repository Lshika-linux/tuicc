"""Launcher module: fuzzy-search .desktop apps by typing from anywhere,
launch the selection on the workspace currently shown in the sidebar
and preview (ctx.focus_id). Also the future home of saved workspace
layouts (save/overwrite/run) — not built yet.
"""

import curses
import os
import subprocess

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


DESKTOP_DIRS = [
    "/run/current-system/sw/share/applications",
    os.path.expanduser("~/.nix-profile/share/applications"),
    os.path.expanduser("~/.local/state/nix/profile/share/applications"),
    os.path.expanduser("~/.local/share/applications"),
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
    "/etc/profiles/per-user/" + os.environ.get("USER", "") + "/share/applications",
    "/nix/var/nix/profiles/default/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/usr/share/applications",
]

_apps_cache = None


def scan_desktop_apps():
    """Scan DESKTOP_DIRS for .desktop files, return a sorted list of
    (name, exec_command) tuples. %-prefixed Exec= tokens (%f, %u, %U,
    %i...) are dropped rather than interpreted, since tuicc launches
    apps with no file/URL argument to pass them. Entries with
    NoDisplay=true, or missing Name/Exec, are skipped.
    """
    apps = []
    seen = set()
    for d in DESKTOP_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith(".desktop") or fname in seen:
                continue
            seen.add(fname)
            name = None
            exec_cmd = None
            no_display = False
            try:
                with open(os.path.join(d, fname), errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("Name=") and name is None:
                            name = line[5:]
                        elif line.startswith("Exec=") and exec_cmd is None:
                            exec_cmd = line[5:]
                        elif line.startswith("NoDisplay=true"):
                            no_display = True
            except OSError:
                continue
            if name and exec_cmd and not no_display:
                clean = " ".join(p for p in exec_cmd.split() if not p.startswith("%"))
                apps.append((name, clean))
    apps.sort(key=lambda a: a[0].lower())
    return apps


def _get_apps():
    global _apps_cache
    if _apps_cache is None:
        _apps_cache = scan_desktop_apps()
    return _apps_cache


def _fuzzy_score(query, target):
    """Subsequence fuzzy match: every character in query must appear in
    target, in order, not necessarily contiguous. Returns None if no
    match; otherwise a score where lower is better (tighter span,
    earlier start).
    """
    query = query.lower()
    target_l = target.lower()
    ti = 0
    positions = []
    for qc in query:
        found = target_l.find(qc, ti)
        if found == -1:
            return None
        positions.append(found)
        ti = found + 1
    if not positions:
        return 0
    span = positions[-1] - positions[0]
    return span + positions[0]


def filter_apps(query, apps):
    if not query:
        return apps
    scored = []
    for name, cmd in apps:
        score = _fuzzy_score(query, name)
        if score is not None:
            scored.append((score, name, cmd))
    scored.sort(key=lambda t: (t[0], t[1].lower()))
    return [(name, cmd) for _score, name, cmd in scored]


def resolve_selected(query, selected_index):
    """The exec command for the currently selected search result, or
    None if there are no results. Spawning it and getting it onto the
    right workspace is main.py's job — it needs to snapshot window ids
    before launching, which is loop-level state this module doesn't
    have.
    """
    results = filter_apps(query, _get_apps())
    if not results:
        return None
    index = min(selected_index, len(results) - 1)
    _name, cmd = results[index]
    return cmd


def _build_window(results, sel, avail_w):
    """Which result indices fit in avail_w, starting from 0 — unless
    the selected index doesn't fit in that window, in which case the
    window is recomputed to start right on it (snap scrolling, no
    offset to track between frames).
    """
    def item_width(name):
        return 4 + len(name[:14]) + 2

    def build(start):
        cx, shown = 0, []
        for i in range(start, len(results)):
            iw = item_width(results[i][0])
            if cx + iw > avail_w and shown:
                break
            shown.append(i)
            cx += iw
        return shown

    shown = build(0)
    if sel not in shown:
        shown = build(sel)
    return shown


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Launcher")

    if not ctx.typing_mode:
        hint = "start typing to search apps…"
        hint_x = x + 1 + max((w - 2 - len(hint)) // 2, 0)
        hint_y = y + h // 2
        try:
            stdscr.addstr(hint_y, hint_x, hint[:max(w - 2, 0)], theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass
        return

    query_row = y + 1
    items_row = y + 2 if h > 3 else y + 1
    avail_w = max(w - 4, 0)

    query_text = f"> {ctx.search_query}"
    try:
        stdscr.addstr(query_row, x + 2, query_text[:avail_w], theme.get("accent", 0) | curses.A_BOLD)
    except curses.error:
        pass

    results = filter_apps(ctx.search_query, _get_apps())
    if not results:
        try:
            stdscr.addstr(items_row, x + 2, "(no match)", theme.get("urgent", 0))
        except curses.error:
            pass
        return

    sel = min(ctx.search_selected_index, len(results) - 1)
    shown = _build_window(results, sel, avail_w)

    cx = x + 2
    for i in shown:
        name, _cmd = results[i]
        letter = (name.strip()[:1] or "?").upper()
        label = name[:14]
        is_sel = (i == sel)
        badge_color = theme.get("selected", 0) if is_sel else theme.get("accent", 0)
        text_color = theme.get("selected", 0) if is_sel else theme.get("text", 0)
        try:
            stdscr.addstr(items_row, cx, f"[{letter}]", badge_color | curses.A_BOLD)
            stdscr.addstr(items_row, cx + 4, label, text_color)
        except curses.error:
            pass
        cx += 4 + len(label) + 2

    if shown and shown[-1] < len(results) - 1:
        remaining = len(results) - 1 - shown[-1]
        try:
            stdscr.addstr(items_row, cx, f"+{remaining}", theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    return []
