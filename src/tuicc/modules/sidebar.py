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
import time

from tuicc.navigation import NavItem, LAST_ITEM_QUERY
from tuicc.render_utils import draw_box_outline, display_width, wc_truncate, marquee_text
from tuicc.title_condense import condense_title


def _grouped_window_rows(windows, cfg):
    """Pure logic: collapse windows that would show the exact same row
    (app_id + condensed detail) into one row with a count, instead of
    repeating it once per window — found live, an autotiling spiral
    gone deep (16+ plain "kitty ~" shells, this session's own machine)
    wastes a whole screen's worth of sidebar height on rows that say
    nothing different from each other. Genuinely different windows of
    the same app (one running htop, say) stay their own row — only
    EXACT duplicates collapse, so nothing actually informative gets
    hidden.

    The grouping key also includes tab_group_id (GitHub issue #8
    follow-up, found live 2026-08-31): otherwise-identical bare shells
    that happen to sit in DIFFERENT stacked/tabbed containers (or one
    in a group and one not in any) would collapse together into one
    misleading count — a "×8" that's actually "7 in one stacked group,
    1 sitting alone elsewhere" reads as one thing when it's really two.

    Two more things happen here for the same underlying reason (Rafi's
    own live ask, same session): rows are also REORDERED so every row
    belonging to a real group sits adjacent to its own group's other
    rows (grouped rows first, in first-seen GROUP order; ungrouped
    rows last) — a group's rows landing scattered among ordinary
    windows, in whatever order region.windows happened to report them,
    made it hard to even recognize as one group at a glance. And each
    distinct group_id gets its own numbered, layout-specific label
    ("S1"/"S2" for separate stacked containers, "T1"/"T2" for separate
    tabbed ones, independent counters) instead of a bare
    "stacked"/"tabbed" — needed once a workspace has two separate
    stack/tab systems side by side, so their rows don't read as one
    ambiguous group. No brackets — see draw()'s own call site for why.

    Returns [(app_id, detail, count, group_label), ...] — group_label
    is None for an ungrouped row, else "S<N>"/"T<N>" as above.
    count is always >= 1; the caller only appends a "×N" suffix when
    it's > 1 (see draw()'s own call site) — a count of exactly 1
    renders identically to the original, uncollapsed row.
    """
    groups = {}
    order = []
    for window in windows:
        key = (window.app_id, condense_title(window.app_id, window.title, cfg), window.tab_group_id)
        if key not in groups:
            groups[key] = {"count": 0, "layout": window.tab_group_layout, "group_id": window.tab_group_id}
            order.append(key)
        groups[key]["count"] += 1

    group_labels = {}
    group_seen_order = []
    counters = {"stacked": 0, "tabbed": 0}
    for key in order:
        gid = groups[key]["group_id"]
        if gid is not None and gid not in group_labels:
            layout = groups[key]["layout"]
            counters[layout] += 1
            letter = "S" if layout == "stacked" else "T"
            # No brackets — that's already the visual convention
            # preview.py's own corner labels use ("[K-1]", "[K-i]",
            # see _group_corner_labels() there); reusing it here read
            # as colliding with that, different, thing (Rafi's own
            # live call, 2026-08-31).
            group_labels[gid] = f"{letter}{counters[layout]}"
            group_seen_order.append(gid)

    group_rank = {gid: i for i, gid in enumerate(group_seen_order)}

    def sort_key(key):
        gid = groups[key]["group_id"]
        # (0, rank) sorts every grouped row before any ungrouped row
        # (1, 0); sorted() is stable, so rows sharing a rank (same
        # group, or all the ungrouped ones together) keep their
        # original first-seen relative order.
        return (0, group_rank[gid]) if gid is not None else (1, 0)

    rows = []
    for key in sorted(order, key=sort_key):
        app_id, detail, _group_id = key
        info = groups[key]
        rows.append((app_id, detail, info["count"], group_labels.get(info["group_id"])))
    return rows


def _slot_height(region, cfg, preview_count=0):
    base = 2 if region is None else 2 + len(_grouped_window_rows(region.windows, cfg))
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


def slot_ids(regions, wm_config, total_workspaces, workspace_mode="autodetect", manual_workspace_names=None) -> list[str]:
    """The ordered, de-duplicated set of workspace identities to show
    as slots — real regions are always included (union), never
    silently hidden regardless of source. See GitHub issue #9 /
    CLAUDE/NOTES/design-decisions.md#workspace-config-parsing for the
    full story: total_workspaces-many numbered slots used to be the
    ONLY source, so a workspace named "20" or "chat" (anything outside
    "1".."total_workspaces") simply never appeared.

    Three possible sources for the base list, in priority order:
    1. workspace_mode="manual": manual_workspace_names, verbatim — the
       explicit escape hatch for whatever autodetect genuinely can't
       see (a dynamically exec-generated binding, no static
       declaration anywhere at all). config.py already refuses to
       load with this mode set and an empty/missing list, so a real
       value is guaranteed here whenever this branch is taken.
    2. wm_config.workspace_names (parsed from the WM's own config text
       — bindsym/for_window/assign, see wm_config_parser.py) when
       that's available and non-empty — the "autodetect" default.
    3. The old "1".."total_workspaces" guess — unparseable/unsupported
       WM config, exactly the original behavior, unchanged.

    Whichever source wins, any region that genuinely exists right now
    but isn't already in that base list is appended at the end — a
    workspace the chosen source missed for any reason (a dynamically
    generated binding under autodetect, a stale manual list, a
    workspace renamed at runtime) still shows up the moment it's
    actually used, never silently dropped.
    """
    if workspace_mode == "manual" and manual_workspace_names:
        ids = list(manual_workspace_names)
    elif wm_config is not None and wm_config.workspace_names:
        ids = list(wm_config.workspace_names)
    else:
        ids = [str(n) for n in range(1, total_workspaces + 1)]
    seen = set(ids)
    for region in regions:
        if region.id not in seen:
            seen.add(region.id)
            ids.append(region.id)
    return ids


def _build_slots(ctx):
    """Return a list of (workspace_id, region_or_None) for every slot —
    see slot_ids()'s own docstring for how that set is determined.
    """
    by_id = {region.id: region for region in ctx.state.regions}
    ids = slot_ids(
        ctx.state.regions, ctx.wm_config, ctx.config.total_workspaces,
        ctx.config.workspace_mode, ctx.config.workspace_names,
    )
    return [(ws_id, by_id.get(ws_id)) for ws_id in ids]


def _selected_slot_index(slots, selected_id) -> int | None:
    """Which 0-indexed slot ctx.selected_id currently points at, or
    None if selection isn't on this module at all (tabbed elsewhere —
    same "window resets to the top" behavior sysmon.py's own
    _selected_window_index gives _visible_slot_range below, deliberate
    and consistent, not a gap: nothing case (ctx.selected_id belongs to
    a different module) needs a stale scroll position remembered
    across it).

    navigation.LAST_ITEM_QUERY is a special case, not "not selected":
    main.py re-queries nav_items() with this in place of a real
    selected_id specifically when rolling backward (Shift+Tab) into
    sidebar from another module — see that constant's own docstring
    for why plain list-scanning can't find the true last item on its
    own. Returning the true last index here (not None) makes
    _visible_slot_range anchor its window on it directly, the same way
    a real selection on that slot would.
    """
    if selected_id == LAST_ITEM_QUERY:
        return len(slots) - 1 if slots else None
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


def shift_workspace_id(current_id, ids: list[str], delta: int):
    """current_id shifted by delta within ids (the real slot_ids() list
    — GitHub issue #9's fix: this used to be pure "1..total_workspaces"
    modulo arithmetic, blind to any non-numeric or out-of-range real
    workspace name), wrapping at either end. Used by main.py's
    mode_stack "launcher" tier to let Up/Down move the ambient-typing
    launch target while still typing, without leaving typing mode.

    current_id may be None, or simply not present in ids (a workspace
    slot_ids() never generated a placeholder for, e.g. one dynamically
    created and then renamed past whatever the WM config parser saw) —
    falls back to ids[0] for delta=+1 / ids[-1] for delta=-1 rather
    than raising, since this is UI convenience, not something that
    should ever crash the render loop. (Old behavior for this same
    "invalid current" case treated it as if positioned at slot 1 and
    then shifted from there, landing one further along than this;
    landing squarely on the first/last real slot instead reads more
    sensibly now that "the slots" is a real, WM-declared list rather
    than a guessed numeric range.)
    """
    if not ids:
        return current_id
    try:
        index = ids.index(current_id)
    except ValueError:
        index = -1 if delta >= 0 else 0
    new_index = (index + delta) % len(ids)
    return ids[new_index]


def _slot_data(ctx):
    """(ws_id, region, preview_apps, height) for every slot — the one
    place draw()/nav_items() both build this from, so the two can never
    drift apart the way two independent _build_slots()+_slot_height()
    call sites once could (each recomputed preview_apps separately).
    """
    data = []
    for ws_id, region in _build_slots(ctx):
        preview_apps = _preview_apps_for(ctx, ws_id)
        data.append((ws_id, region, preview_apps, _slot_height(region, ctx.config, len(preview_apps))))
    return data


def _hidden_summary(slots, indices) -> str:
    """"+N ws, +M win" for the slots at these indices — how many
    workspaces AND how many real windows inside them are currently
    scrolled out of view. Window count matters on its own, not just
    workspace count: "+1 workspace" undersells it when that one hidden
    workspace happens to hold a dozen windows. "" (falsy, draw() treats
    it as "nothing to show") when indices is empty.
    """
    indices = list(indices)
    if not indices:
        return ""
    ws_count = len(indices)
    win_count = sum(len(slots[i][1].windows) for i in indices if slots[i][1] is not None)
    return f"+{ws_count} ws, +{win_count} win" if win_count else f"+{ws_count} ws"


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    now = time.time()  # marquee_text()'s own clock — see the detail-line loop below

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)

    slots = _slot_data(ctx)
    heights = [s[3] for s in slots]
    selected_index = _selected_slot_index([(s[0], s[1]) for s in slots], ctx.selected_id)
    start, end = _visible_slot_range(heights, selected_index, max(h - 2, 0))

    # Plain title here — no hidden-content indicator embedded in this
    # call. draw_hidden_indicators() below redraws just the border a
    # second time, later in the SAME frame (see its own docstring for
    # why it has to be a separate, later pass rather than folded in
    # here) — reusing draw_box_outline's own title/bottom_label
    # mechanism, not duplicating it.
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Workspaces")

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
            # Collapses windows that would show the exact same row
            # (e.g. many plain "kitty ~" shells) into one — see
            # _grouped_window_rows()'s own docstring.
            grouped = _grouped_window_rows(region.windows, ctx.config)
            for i, (app, detail, count, group_label) in enumerate(grouped):
                app_label = f"{app} ×{count}" if count > 1 else app
                available = max(w - 4, 0)

                try:
                    cx = x + 2
                    if group_label:
                        # Flags a collapsed row (or even a lone one) as
                        # belonging to a real stacked/tabbed group,
                        # numbered per distinct group instance ("S1",
                        # "T2", ...) — a PREFIX at the very start of
                        # the row, not a suffix after the app name, and
                        # deliberately DIM rather than bold like the app
                        # name below (Rafi's own live call, 2026-08-31
                        # — asked for plain first, then dim once seen
                        # live) — its own separate addstr so it can
                        # carry its own, quieter style. No brackets
                        # either, that's
                        # already preview.py's own corner-label
                        # convention (see _grouped_window_rows()'s own
                        # docstring, which also covers why group
                        # membership drives the row ORDER, not just
                        # this label).
                        prefix_chunk = wc_truncate(f"{group_label} ", available)
                        stdscr.addstr(item_y + 1 + i, cx, prefix_chunk, text_color | curses.A_DIM)
                        cx += display_width(prefix_chunk)

                    chunk = wc_truncate(app_label, max(available - (cx - (x + 2)), 0))
                    stdscr.addstr(item_y + 1 + i, cx, chunk, text_color | curses.A_BOLD)
                    cx += display_width(chunk)
                    # row_end, not end — `end` is this function's own
                    # outer slot-range boundary (from _visible_slot_range).
                    row_end = x + 2 + available
                    if detail and cx + 1 < row_end:
                        # app_id (above) stays fixed, only the detail
                        # part scrolls when it's the one too long to
                        # fit — same fixed-prefix/scrolling-body split
                        # media.py's Now Playing row already uses (see
                        # marquee_text()'s own docstring in
                        # render_utils.py). -1 reserves the leading
                        # space this line always draws before detail.
                        detail_w = max(row_end - cx - 1, 0)
                        scrolled = marquee_text(detail, detail_w, now)
                        stdscr.addstr(item_y + 1 + i, cx, f" {scrolled}", text_color | curses.A_DIM)
                except curses.error:
                    pass
            existing_count = len(grouped)

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


def _fitting_title(base: str, indicator: str, w: int) -> str:
    """The most informative TOP-border title that actually fits w
    columns — "base indicator" combined, else indicator alone
    (dropping the now-redundant base — "Workspaces" is already obvious
    from context once scrolled, and the indicator is the more urgent
    information when both can't fit), else bare base as the last
    resort (still better than draw_box_outline's own blank-dashes
    fallback for a title that doesn't fit at all).

    Only ever used for the TOP row: sidebar OWNS that title outright
    (see draw_hidden_indicators()'s own docstring for why the top and
    bottom rows need genuinely different treatment) — redrawing it
    combined is safe because there's no OTHER module's own real
    content on that row to preserve, just sidebar's own title.
    """
    available = max(w - 2, 0)
    for candidate in (f"{base} {indicator}".strip(), indicator, base):
        if candidate and display_width(f" {candidate} ") < available:
            return candidate
    return base


def _right_aligned_overlay_col(x: int, w: int, text: str, min_left_col: int) -> int | None:
    """Where text should start to sit right-aligned near the box's own
    right corner (1 dash of margin before the corner, matching the 1
    dash draw_box_outline's own title already keeps after the left
    corner) — or None if it doesn't fit there without colliding with
    min_left_col (whatever's already occupying the left side of this
    same row — see draw_hidden_indicators()'s own bottom-row case for
    why that has to stay a conservative estimate rather than exact
    knowledge).
    """
    col = x + w - 1 - display_width(text) - 1
    return col if col > min_left_col else None


def draw_hidden_indicators(stdscr, box, ctx, module_name):
    """"+N ws, +M win" wherever the scrollable window doesn't already
    show everything — TOP row (content hidden above) and/or BOTTOM row
    (hidden below). See draw() above for the plain first pass, and
    CLAUDE/NOTES/design-decisions.md#sidebar-hidden-content-indicator
    for why this has to be its own separate, LATER call rather than
    part of draw() itself. main.py calls this once, unconditionally,
    right after draw_all() returns — same "draw directly onto stdscr
    after/instead of draw_all(), not through the normal per-module
    pass" idiom this codebase already uses for resize mode's editing
    highlight and help_mode's panel (see CLAUDE/GUIDE.md's own
    architecture notes). Being the literal last thing drawn on this
    box's top/bottom rows for the whole frame is the entire point:
    this codebase's own tightly-packed presets commonly place another
    module's box directly adjacent with zero gap, sharing that exact
    row — whichever module draws LATER in MODULES' own normal
    iteration order would otherwise win it, silently erasing anything
    embedded there during the normal per-module pass.

    TOP and BOTTOM genuinely need DIFFERENT treatment, found live, not
    a stylistic choice: sidebar's own top row is ALSO shared (with
    whatever's directly above — Sessions, in the default preset), but
    Sessions never puts real content there (plain dashes only), so
    it's safe to fully redraw combined with "Workspaces" via
    _fitting_title() + draw_box_outline(), degrading gracefully by
    dropping "Workspaces" if both don't fit (confirmed needed live —
    "Workspaces +7 ws, +9 win" routinely doesn't fit a sidebar-width
    box). The BOTTOM row is different: whatever's directly below
    (Control, in the default preset) has its OWN real title drawn
    there, which a full redraw would silently clobber (confirmed live:
    the first version did exactly this, chopping "Control" mid-word) —
    so the bottom row gets a narrower, OVERLAY-only treatment instead,
    writing just its own cells, right-aligned, with a conservative
    left margin standing in for knowledge sidebar can't have about
    another module's real title width without violating "core never
    guesses a module's internal layout" in the other direction.
    """
    x, y, w, h = box
    theme = ctx.theme or {}

    slots = _slot_data(ctx)
    heights = [s[3] for s in slots]
    selected_index = _selected_slot_index([(s[0], s[1]) for s in slots], ctx.selected_id)
    start, end = _visible_slot_range(heights, selected_index, max(h - 2, 0))

    above_summary = _hidden_summary(slots, range(0, start))
    below_summary = _hidden_summary(slots, range(end, len(slots)))
    if not above_summary and not below_summary:
        return

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)

    if above_summary:
        title = _fitting_title("Workspaces", above_summary, w)
        draw_box_outline(stdscr, y, x, h, w, outer_color, title=title)

    if below_summary:
        text = f" {below_summary} "
        # Reserving half the box's own width (or 12 columns, whichever
        # is more) as a left margin is a heuristic, not a proof — it
        # clears every real title in this codebase's own default
        # preset (confirmed live against "Control" specifically), but
        # a sufficiently long custom title on whatever sits below in a
        # DIFFERENT preset could still collide. See this function's
        # own docstring for why exact knowledge isn't available here.
        min_left_col = x + max(w // 2, 12)
        col = _right_aligned_overlay_col(x, w, text, min_left_col)
        if col is not None:
            try:
                stdscr.addstr(y + h - 1, col, text, outer_color)
            except curses.error:
                pass


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
