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
from tuicc.keybinds import key_label


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
    # The launcher's own placement-mode picker (CLAUDE/NOTES/
    # design-decisions.md#launcher-placement-mode) — "tiled" (default,
    # today's exact behavior), "stack_new"/"tab_new" (group with
    # whatever's already on the target workspace), an existing group's
    # own label ("S1"/"T1"/... — Provider.list_container_groups()'s own
    # shape), or "floating". Cycled by Tab/Shift+Tab in main.py's
    # handle_launcher() (inert during typing otherwise), reset to
    # default_placement_mode()'s own answer every time the target
    # region changes (Up/Down, or once right after typing starts) —
    # never sticky across a workspace change, since a group label only
    # means something on the workspace it came from.
    placement_mode: str = "tiled"


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


def placement_mode_options(groups: list[dict]) -> list[str]:
    """The full, ordered placement-mode cycle for a target region whose
    current existing groups are `groups` (Provider.list_container_groups()'s
    own shape) — tiled, then "start a new stack/tab here", then one
    entry per EXISTING group by its own label (S1/S2/T1/...), then
    floating. Rafi's own example ordering, verbatim: "tiled, stacked,
    tabbed, into S1, into S2, into T1, into T2, floating".
    """
    return ["tiled", "stack_new", "tab_new"] + [g["label"] for g in groups] + ["floating"]


def default_placement_mode(groups: list[dict]) -> str:
    """The natural starting choice for a target region: its own FIRST
    existing group if it has one (you just made a stack — you probably
    want to keep adding to it), else plain "tiled" (today's exact
    behavior, unchanged when nothing's grouped yet).
    """
    return groups[0]["label"] if groups else "tiled"


def placement_mode_display(mode: str) -> str:
    """Human-friendly text for the sidebar's own "launching here (...)"
    label (modules/sidebar.py) — "S1"/"T1"/etc already read naturally on
    their own ("into S1"), everything else needs a small friendly name.
    """
    if mode == "stack_new":
        return "stacked (new)"
    if mode == "tab_new":
        return "tabbed (new)"
    if mode in ("tiled", "floating"):
        return mode
    return f"into {mode}"


def _row_label(mode: str) -> str:
    """Short, row-specific label for one placement_mode_options() entry
    — the 4 fixed modes get a plain capitalized word (matching the
    row's own established look), a real existing group's own label
    ("S1"/"T1"/...) reuses placement_mode_display()'s "into S1" text
    verbatim (asked for live: "taky by se v tom seznamu měl objevovat
    into S1, T1 cokoliv if applicable" — every REAL option the picker
    can reach shows up as its own entry, not collapsed into a generic
    "Stacked"/"Tabbed" bucket).
    """
    if mode == "tiled":
        return "Tiled"
    if mode == "stack_new":
        return "Stacked"
    if mode == "tab_new":
        return "Tabbed"
    if mode == "floating":
        return "Floating"
    return placement_mode_display(mode)


def _draw_placement_row(stdscr, row, x, w, mode, options, tab_key_label, theme, prefix=None):
    """Draws "[Tab] [● Tiled] [○ Stacked] [○ into S1] [○ Tabbed]
    [○ Floating]" on row — one entry per item in options
    (placement_mode_options()'s own live-computed order, including any
    real existing group for the current target region, not just the 4
    fixed modes) — one addstr call per differently-colored piece
    (curses can't mix colors within a single addstr string): the
    active entry's own bracket group lit in accent/bold with a filled
    ● dot, every other stays dim with a hollow ○, same ●/○ "is this
    the current state" convention modules/connectivity.py's own header
    legend already established. Stops drawing once a piece would cross
    the box's own inner right edge, same "truncate the row, don't
    overflow into whatever's next door" discipline the rest of this
    codebase applies — never raises past that; a curses.error on any
    individual write (a genuinely too-narrow terminal) is swallowed
    the same way every other draw call in this module already does.

    prefix (default None): the routing-rule hint, drawn first — asked
    for live, once it became clear a real box has plenty of spare
    WIDTH on this exact row even when it's genuinely short on HEIGHT
    ("druhý řádek je uplně prázdný" — the row this already draws on
    sat mostly empty) — sharing the row horizontally needs no extra
    row at all, unlike the earlier (rejected) attempt to literally
    replace this row's own content with the rule's.
    """
    right_edge = x + w - 1
    cx = x + 2
    dim = theme.get("text", 0) | curses.A_DIM
    lit = theme.get("accent", 0) | curses.A_BOLD

    def put(text, color):
        nonlocal cx
        if cx >= right_edge:
            return False
        try:
            stdscr.addstr(row, cx, wc_truncate(text, max(right_edge - cx, 0)), color)
        except curses.error:
            pass
        cx += display_width(text)
        return cx < right_edge

    if prefix and not put(f"{prefix}    ", dim):
        return
    if not put(f"[{tab_key_label}] ", dim):
        return
    for option in options:
        is_active = option == mode
        color = lit if is_active else dim
        dot = "●" if is_active else "○"
        if not put(f"[{dot} {_row_label(option)}] ", color):
            return


def cycle_placement_mode(current: str, options: list[str], step: int) -> str:
    """Moves current by step (+1/-1) through options, wrapping both
    directions. Falls back to options[0] if current isn't in the list
    at all (e.g. the target region changed and current was a group
    label — "S1" — that only ever meant something on the PREVIOUS
    workspace; main.py resets placement_mode outright on a region
    change anyway, this is just a defensive fallback, never load-
    bearing in practice) — never raises on an unexpected value.
    """
    if not options:
        return current
    try:
        index = options.index(current)
    except ValueError:
        return options[0]
    return options[(index + step) % len(options)]


def _placement_hint_position(box_x: int, box_w: int, query_width: int, mode_width: int) -> int | None:
    """Where draw() should put the placement-mode hint text (e.g.
    "[stacked (new)]") on the launcher's own query row, right-aligned
    against the box's own inner right edge — or None if there isn't
    room without it overlapping the search query text itself. The
    query always wins that fight (a search in progress is the more
    important thing on that row) — this returns None rather than some
    truncated, half-legible mode label crammed in wherever it fits.
    """
    right_edge = box_x + box_w - 2
    mode_x = right_edge - mode_width
    query_end = box_x + 2 + query_width
    if mode_x <= query_end + 1:
        return None
    return mode_x


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
    state.placement_mode = "tiled"


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
    sel = min(ctx.search_selected_index, len(results) - 1) if results else None
    sel_app_id = results[sel][2] if sel is not None else None

    # GitHub issue #9's routing-rule follow-on: a status line, not a
    # new keybind — Up/Down (not a new [TAB] binding) already move
    # focus_id without leaving typing mode (see main.py's own
    # handle_launcher() comment), so the hint just names the key that
    # already does this. ctx.focus_id, by the time draw() runs this
    # frame, already reflects whatever main.py's handle_launcher() just
    # decided (its own auto-default, or the user's manual Up/Down
    # override) — showing it directly here, rather than re-deriving the
    # same precedence logic a second time, keeps this a pure "what will
    # actually happen" readout.
    routing_target = None
    if sel_app_id is not None and ctx.wm_config and sel_app_id in ctx.wm_config.routing_rules:
        routing_target = ctx.focus_id if ctx.focus_id is not None else ctx.state.focused_region_id
    routing_hint = f'Routing rule — ws "{routing_target}"' if routing_target is not None else None

    # Placement-mode row (CLAUDE/NOTES/design-decisions.md
    # #launcher-placement-mode) — its own dedicated row, right under
    # the query, whenever the box is tall enough to spare one. The
    # routing-rule hint (GitHub issue #9's own follow-on) shares this
    # SAME row, prefixed on the left, rather than needing a row of its
    # own — found live, once it was actually pointed at: this row has
    # plenty of spare WIDTH even on a box that's genuinely short on
    # HEIGHT, so there was never a real height problem to solve here in
    # the first place (an earlier attempt at a genuinely separate row
    # was scrapped the same round). A tiny box with no room for a
    # dedicated row at all falls back to squeezing just the rule (the
    # more urgent of the two) onto the query row's own right edge.
    show_mode_row = h > 4
    if show_mode_row:
        mode_row = y + 2
        items_row = y + 3
        options = ctx.launcher_placement_options or [ctx.launcher_placement_mode]
        _draw_placement_row(
            stdscr, mode_row, x, w, ctx.launcher_placement_mode, options,
            key_label(ctx.config.keybinds["tab"]), theme, prefix=routing_hint,
        )
    else:
        # Tiny box, no room for a dedicated row at all — same
        # "the rule wins the one available slot" priority as above,
        # squeezed onto the query row's own right edge instead; see
        # _placement_hint_position()'s own docstring for why a long
        # in-progress search silently wins over showing either of them.
        items_row = y + 2 if h > 3 else y + 1
        text = routing_hint if routing_hint is not None else f"[{placement_mode_display(ctx.launcher_placement_mode)}]"
        text_x = _placement_hint_position(x, w, display_width(wc_truncate(query_text, avail_w)), display_width(text))
        if text_x is not None:
            try:
                stdscr.addstr(query_row, text_x, text, theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

    if not results:
        try:
            stdscr.addstr(items_row, x + 2, "(no match)", theme.get("urgent", 0))
        except curses.error:
            pass
        return

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


def nav_items(box, ctx, module_name) -> list[NavItem]:
    return []
