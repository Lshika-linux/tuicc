"""Launcher module: fuzzy-search .desktop apps by typing from anywhere,
launch the selection on the workspace currently shown in the sidebar
and preview (ctx.focus_id). Also the future home of saved workspace
layouts (save/overwrite/run) — not built yet.

LauncherState and the functions below it are the typing-mode session
layer — what main.py's loop used to hold as five loose local variables
(typing_mode/search_query/search_selected_index/saved_selected_id/
saved_active_module) plus inline key-handling. Same "pure function over
an explicit value" style as resize_mode.py/help_mode.py: a dataclass is
just the state, every function here takes one and mutates it, main.py
still owns *when* to call them (and still owns active_module itself —
see enter_typing_mode's docstring for why that one field stays outside
this dataclass).
"""

import curses
import os
import subprocess
from dataclasses import dataclass

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, display_width, wc_truncate


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
    (name, exec_command, app_id_hint) tuples. %-prefixed Exec= tokens
    are dropped (tuicc launches with no file/URL argument to pass
    them). Entries with NoDisplay=true, or missing Name/Exec, are
    skipped. app_id_hint is StartupWMClass= when set, else the file's
    basename — used by pending_moves.py's app_id tier as a fallback
    match signal for apps whose spawned pid never matches any window's
    (a single-instance app asking an already-running instance to open
    a window, then exiting itself).
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
            wm_class = None
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
                        elif line.startswith("StartupWMClass=") and wm_class is None:
                            wm_class = line[len("StartupWMClass="):]
            except OSError:
                continue
            if name and exec_cmd and not no_display:
                clean = " ".join(p for p in exec_cmd.split() if not p.startswith("%"))
                app_id_hint = wm_class or fname[:-len(".desktop")]
                apps.append((name, clean, app_id_hint))
    apps.sort(key=lambda a: a[0].lower())
    return apps


def _get_apps():
    global _apps_cache
    if _apps_cache is None:
        _apps_cache = scan_desktop_apps()
    return _apps_cache


def get_apps():
    """Public accessor for the same cached (name, exec_command,
    app_id_hint) list _get_apps() already maintains for this module's
    own fuzzy search — sysmon.py's own _friendly_app_name() reuses this
    (a real window's app_id -> its .desktop entry's own display name)
    rather than re-scanning DESKTOP_DIRS itself or keeping a second,
    separate cache. Every other cross-module call in this codebase goes
    through a public function (sessions_mode.is_expanded(), etc.), not
    another module's own underscore-prefixed internals — this exists so
    that convention holds here too.
    """
    return _get_apps()


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
    for name, cmd, app_id in apps:
        score = _fuzzy_score(query, name)
        if score is not None:
            scored.append((score, name, cmd, app_id))
    scored.sort(key=lambda t: (t[0], t[1].lower()))
    return [(name, cmd, app_id) for _score, name, cmd, app_id in scored]


@dataclass
class LauncherState:
    """typing_mode/search_query/search_selected_index are the live
    editing fields, reset on every entry/exit. saved_selected_id/
    saved_active_module sit at their None default whenever typing_mode
    is False — enter_typing_mode populates them, exit_typing_mode never
    touches them, so the caller (main.py) can read them right after to
    restore selected_id/active_module, same as it does after a
    successful confirm.
    """
    typing_mode: bool = False
    search_query: str = ""
    search_selected_index: int = 0
    saved_selected_id: str | None = None
    saved_active_module: str | None = None
    # GitHub issue #9's routing-rule follow-on: once the user presses
    # Up/Down during typing, main.py stops auto-forcing focus_id to
    # whatever routed_target() says FOR THAT SPECIFIC APP — their own
    # pick wins whenever this exact app_id stays selected. Scoped to
    # the app, not the whole typing session: live-found, arrowing away
    # from one app with no rule (or a rule you don't want right now)
    # must not silently suppress a DIFFERENT app's own rule once the
    # selection moves on to it — "spawning somewhere else" is only
    # ever a deliberate choice about the app you made it for. Until
    # overridden, every keystroke unconditionally re-forces focus_id
    # to the routed target (see pre_routing_focus_id below for what
    # happens when nothing routes), overwriting any stale sticky value
    # focus_id already had — that stickiness (preview.py showing the
    # last real workspace pick across unrelated modules) is exactly
    # why "only touch it if it's still None" doesn't work here; it's
    # essentially never None by the time typing starts. Reset to None
    # on both typing-mode boundaries.
    manual_target_app_id: str | None = None
    # The real launch target as it stood right BEFORE typing started —
    # snapshotted once by enter_typing_mode(). When the currently
    # selected app has no routing rule (routed_target() is None),
    # main.py's _apply_launcher_routing_default() reverts focus_id to
    # THIS, not just leaves it alone. Live-found needed: a ruled app
    # (auto-routed to its workspace) selected, then the selection
    # moves on to an unruled one — without a revert, focus_id stayed
    # stuck on the earlier rule's target forever, even though nothing
    # about the current selection has anything to do with it anymore.
    # Reset to None on both typing-mode boundaries, same as
    # manual_target_app_id.
    pre_routing_focus_id: str | None = None


def resolve_selected(state: LauncherState):
    """The (exec_command, app_id_hint) for the currently selected search
    result, or None if there are no results. Spawning it and getting it
    onto the right workspace is main.py's job — it needs to snapshot
    window ids before launching, which is loop-level state this module
    deliberately doesn't have. app_id_hint (see scan_desktop_apps) lets
    main.py's pending_moves matching fall back to an app_id match if
    the spawned process's own pid never shows up on any window.
    """
    results = filter_apps(state.search_query, _get_apps())
    if not results:
        return None
    index = min(state.search_selected_index, len(results) - 1)
    _name, cmd, app_id = results[index]
    return cmd, app_id


def routed_target(state: LauncherState, wm_config) -> str | None:
    """The workspace a for_window/assign rule (wm_config_parser.py)
    would route the CURRENTLY selected search result's app to, or None
    if nothing's selected or no rule matches its app_id_hint. Pure
    lookup — main.py's handle_launcher() decides whether/when it's
    still allowed to apply this to loop_state.focus_id (see
    LauncherState.manual_target_app_id's own docstring), the same loop-level
    concern resolve_selected() above already keeps out of this module.
    """
    if not wm_config or not wm_config.routing_rules:
        return None
    selected = resolve_selected(state)
    if selected is None:
        return None
    _cmd, app_id_hint = selected
    if app_id_hint is None:
        return None
    return wm_config.routing_rules.get(app_id_hint)


def enter_typing_mode(state: LauncherState, selected_id, active_module, focus_id, initial_query="") -> None:
    """Saves the pre-typing selection so handle_typing_key's Escape/
    Backspace-to-empty exit (or a successful confirm) can restore it
    later. Deliberately does NOT set active_module itself — main.py
    still does `active_module = "launcher"` right next to this call,
    same asymmetry resize_mode.enter_box_editing has for active_module
    (this dataclass owns everything about the typing session except
    the one field that's genuinely main.py's own loop variable).

    focus_id is the CURRENT loop_state.focus_id, snapshotted into
    pre_routing_focus_id before anything in this typing session can
    change it — see that field's own docstring for why. Caller must
    pass it before applying any routing default for the first
    keystroke, or the snapshot would already reflect a routed value
    instead of the real pre-typing one.
    """
    state.saved_selected_id = selected_id
    state.saved_active_module = active_module
    state.typing_mode = True
    state.search_query = initial_query
    state.search_selected_index = 0
    state.manual_target_app_id = None
    state.pre_routing_focus_id = focus_id


def exit_typing_mode(state: LauncherState) -> None:
    """Leaves typing mode, resetting the editable fields. saved_* are
    left untouched — the caller reads them right after this call to
    restore selected_id/active_module, then moves on.
    """
    state.typing_mode = False
    state.search_query = ""
    state.search_selected_index = 0
    state.manual_target_app_id = None
    state.pre_routing_focus_id = None


def handle_typing_key(state: LauncherState, key, cfg) -> bool:
    """Mutates state for the launcher's typing-mode editing keys
    (Escape, Backspace, Left/Right, printable characters). Does NOT
    handle the confirm key — resolving and launching a command needs
    main.py's loop state this module deliberately doesn't have, same
    reasoning as resolve_selected(). Returns still_claiming (True
    unless this call just exited typing mode) — CLAUDE/VISION.md's R2
    input_claim shape; main.py's dispatch reads this directly rather
    than re-checking state.typing_mode afterward.
    """
    if key == 27:  # Escape
        exit_typing_mode(state)
        return False

    if key in (curses.KEY_BACKSPACE, 127, 8):
        if state.search_query:
            state.search_query = state.search_query[:-1]
            state.search_selected_index = 0
        else:
            exit_typing_mode(state)
            return False
        return True

    if key == cfg.keybinds["left"]:
        state.search_selected_index = max(state.search_selected_index - 1, 0)
        return True

    if key == cfg.keybinds["right"]:
        state.search_selected_index += 1
        return True

    if 32 <= key <= 126:
        state.search_query += chr(key)
        state.search_selected_index = 0

    return True


def _build_window(results, sel, avail_w):
    """Which result indices fit in avail_w, starting from 0 — unless
    the selected index doesn't fit in that window, in which case the
    window is recomputed to start right on it (snap scrolling, no
    offset to track between frames).
    """
    def item_width(name):
        # Matches draw()'s own label/cx computation below exactly — a
        # wide/CJK app name has to be measured the same way in both
        # places, or this window-fit decision and what actually gets
        # drawn could disagree.
        return 4 + display_width(wc_truncate(name, 14)) + 2

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
        hint = "start typing to launch apps…"
        hint_x = x + 1 + max((w - 2 - len(hint)) // 2, 0)
        hint_y = y + h // 2
        try:
            stdscr.addstr(hint_y, hint_x, wc_truncate(hint, max(w - 2, 0)), theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass
        return

    query_row = y + 1
    items_row = y + 2 if h > 3 else y + 1
    avail_w = max(w - 4, 0)

    query_text = f"> {ctx.search_query}"
    try:
        # search_query is real, user-typed text — unlike most other
        # payloads in this codebase, it can genuinely contain wide/CJK
        # characters (a non-Latin-script app search), so this one isn't
        # just defensive hygiene the way the static-label sites are.
        stdscr.addstr(query_row, x + 2, wc_truncate(query_text, avail_w), theme.get("accent", 0) | curses.A_BOLD)
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
        name, _cmd, _app_id = results[i]
        letter = (name.strip()[:1] or "?").upper()
        # .desktop app names can genuinely contain wide/CJK characters
        # or emoji — a 14-CODEPOINT slice could measure well past 14
        # real columns, throwing off every following item's position on
        # this row (see cx's own advance below).
        label = wc_truncate(name, 14)
        is_sel = (i == sel)
        badge_color = theme.get("selected", 0) if is_sel else theme.get("accent", 0)
        text_color = theme.get("selected", 0) if is_sel else theme.get("text", 0)
        try:
            stdscr.addstr(items_row, cx, f"[{letter}]", badge_color | curses.A_BOLD)
            stdscr.addstr(items_row, cx + 4, label, text_color)
        except curses.error:
            pass
        cx += 4 + display_width(label) + 2

    if shown and shown[-1] < len(results) - 1:
        remaining = len(results) - 1 - shown[-1]
        try:
            stdscr.addstr(items_row, cx, f"+{remaining}", theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass

    # GitHub issue #9's routing-rule follow-on: a status line, not a
    # new keybind — Up/Down (not a new [TAB] binding) already move
    # focus_id without leaving typing mode (see main.py's own
    # handle_launcher() comment), so the hint just names the key that
    # already does this. ctx.focus_id, by the time draw() runs this
    # frame, already reflects whatever main.py's handle_launcher() just
    # decided (its own auto-default, or the user's manual Up/Down
    # override) — showing it directly here, rather than re-deriving the
    # same precedence logic a second time, keeps this a pure "what will
    # actually happen" readout. Only drawn when there's a spare row
    # below the results (same h > 3 threshold items_row's own fallback
    # already uses).
    _sel_name, _sel_cmd, sel_app_id = results[sel]
    hint_row = items_row + 1
    if (
        sel_app_id is not None and ctx.wm_config and sel_app_id in ctx.wm_config.routing_rules
        and hint_row < y + h - 1
    ):
        target = ctx.focus_id if ctx.focus_id is not None else ctx.state.focused_region_id
        hint = f'Routing rule detected — spawning at ws "{target}"  [↑↓] to change'
        try:
            stdscr.addstr(hint_row, x + 2, wc_truncate(hint, avail_w), theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    return []
