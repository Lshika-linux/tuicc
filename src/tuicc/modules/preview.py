"""Preview module: shows windows of the currently focused workspace,
each drawn as a scaled-down box at its real relative position — which
means overlapping windows (tiled splits sharing an edge, or a floating
window sitting on top of a tiled one) commonly produce overlapping
boxes here too, not just an edge case.

Windows sharing a stacked/tabbed container (Window.tab_group_id, see
model.py's own docstring — GitHub issue #8) are a THIRD, deliberately
different case from the two above: they all report the exact same
rect, so treating them like ordinary overlapping windows would just
stack N identical boxes directly on top of each other. _group_tiled_windows()/
_place_tab_group() give them their own dedicated visual instead — see
those two functions' own docstrings for the shape (a vertical list of
thin title bars for "stacked", a horizontal tab-strip for "tabbed").
The group's ACTIVE slot gets a real content placement, same as any
other window — usually one box, but see Window.tab_slot_id's own
docstring (and _place_slot_content()'s) for why an active slot can
itself be a real, multi-window split needing more than one.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem, module_of_item
from tuicc.render_utils import draw_box_outline, draw_corner_marks, draw_filled_box, draw_centered_lines, centered_x, display_width, wc_truncate, wrap_text
from tuicc.title_condense import condense_title
from tuicc.wm_config_parser import resolve_workspace_target


def _resolved_target_id(ctx):
    """ctx.focus_id if set, else ctx.state.focused_region_id, resolved
    against ctx.wm_config's own known full workspace names (see
    wm_config_parser.resolve_workspace_target()'s own docstring). The
    same value _focused_region() below matches every Region.id against
    — and also what draw() shows as this workspace's own display label
    when there's no live Region to read a name off of at all (the
    never-visited case), doubling as a label the same way sidebar.py's
    own rows do.
    """
    target_id = ctx.focus_id if ctx.focus_id is not None else ctx.state.focused_region_id
    if target_id is None:
        return None
    names = ctx.wm_config.workspace_names if ctx.wm_config is not None else None
    return resolve_workspace_target(target_id, names)


def _focused_region(ctx):
    """The region draw()/nav_items() both target. Resolved against
    ctx.wm_config's own known full workspace names on BOTH sides before
    comparing: region.id is always the bare workspace number
    (providers/sway.py's parse_tree() — a provider's own truthful
    contract, never changes), while _resolved_target_id() above can now
    return a resolved "N:Name" — found live, a bare-vs-resolved
    mismatch here made preview show empty for every populated
    numbered+named workspace. resolve_workspace_target() is a safe
    no-op on an already-resolved value (nothing's leading number equals
    a full "N:Name" string), so resolving region.id unconditionally is
    correct regardless of whether target_id started resolved or bare.
    """
    target_id = _resolved_target_id(ctx)
    if target_id is None:
        return None
    names = ctx.wm_config.workspace_names if ctx.wm_config is not None else None
    for region in ctx.state.regions:
        if resolve_workspace_target(region.id, names) == target_id:
            return region
    return None


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    # Ambient typing (VISION.md's own "start typing from anywhere"
    # identity commitment) always overrides whatever was selected
    # elsewhere before it started — found live, 2026-08-16: a WiFi row
    # (its own real preview_text/preview_data) stayed selected while
    # typing_mode claimed input for the launcher, so preview kept
    # showing stale WiFi diagnostics instead of the one thing actually
    # relevant right now — sidebar.py's own "launching here" label
    # already tracks ctx.focus_id for exactly this reason (see its own
    # comment), preview.py just wasn't reading the same signal.
    # Falling through to the window-preview branch below shows that
    # target workspace instead, the same as no item being selected at
    # all — the stale item's own preview content simply isn't reached
    # while typing, regardless of what it is.
    showing_preview = not ctx.typing_mode and ctx.selected_item is not None and (
        ctx.selected_item.preview_text is not None
        or ctx.selected_item.preview_data is not None
    )

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
        footer = ctx.selected_item.preview_footer

        # Three kinds of stacked areas, top to bottom: an optional
        # module-owned content area (NavItem.preview_data, drawn by
        # whichever render.py's PREVIEW_RENDERERS entry the item's own
        # owning module registered — this file has NO idea what that
        # content looks like, see navigation.py's own preview_data
        # docstring and CLAUDE/NOTES/design-decisions.md
        # #module-self-sufficiency-vs-preview for why), the normal
        # centered preview_text in whatever's left, then an optional
        # boxed-off footer (NavItem.preview_footer — "how do I interact
        # with this" hints) at the very bottom. Each is only given real
        # height when actually present/fits, so a NavItem using none/
        # some/all of preview_data/preview_text/preview_footer just
        # gets exactly that, no reserved blank gaps.
        content_y = y
        content_h = h
        if ctx.selected_item.preview_data is not None:
            owner = module_of_item(ctx.selected_item)
            renderer = ctx.preview_renderers.get(owner)
            if renderer is not None:
                used_h = renderer(stdscr, (x, content_y, w, content_h), ctx.selected_item.preview_data, theme)
                content_y += used_h
                content_h -= used_h

        if footer:
            # A separate, boxed-off strip for "how do I interact with
            # this" hints (see NavItem.preview_footer's own docstring)
            # — a real draw_box_outline(), not just another centered
            # line of preview_text, specifically so it can't blend into
            # the informational content above it. Always urgent-colored
            # regardless of the footer's own per-line colors (same as
            # preview_urgent's whole-border treatment above) — a module
            # asking for this separate strip at all already means "pay
            # attention here", independent of what the text itself says.
            footer_h = min(len(footer) + 2, content_h)
            text_h = content_h - footer_h
            if text_h > 0:
                draw_centered_lines(stdscr, (x, content_y, w, text_h), ctx.selected_item.preview_text)
            footer_y = content_y + text_h
            draw_box_outline(stdscr, footer_y, x, footer_h, w, theme.get("urgent", 0))
            draw_centered_lines(stdscr, (x, footer_y, w, footer_h), footer)
        else:
            draw_centered_lines(stdscr, (x, content_y, w, content_h), ctx.selected_item.preview_text)
        return

    focused_region = _focused_region(ctx)

    # focused_region can be None two different ways that both mean the
    # same thing to the user: a region that exists but lost all its
    # windows, or a workspace number sway/i3 has never actually created
    # a tree node for at all (never visited, never had a window) — the
    # WM simply doesn't report the latter as a region in ctx.state.regions.
    # Both collapse to "nothing to show here", so tiled/floating both
    # stay [] rather than returning early only for the second case.
    tiled = [win for win in focused_region.windows if not win.floating] if focused_region else []
    floating = [win for win in focused_region.windows if win.floating] if focused_region else []

    if not tiled and not floating:
        # A genuinely empty workspace — VISION.md's own "preview idle
        # state" gap (see CLAUDE/NOTES/design-decisions.md
        # #module-self-sufficiency-vs-preview's own "explicitly
        # deferred" note, first raised live and parked as a later
        # design question). A dashed, dim outline standing in for "one
        # window filling this whole workspace" — deliberately NOT the
        # same solid border _draw_window's own real windows use, so it
        # never reads as an actual window, just an absence-shaped
        # placeholder — plus a small, honest caption. Rafi's own ask,
        # verbatim wording and all.
        dim = theme.get("text", 0) | curses.A_DIM
        # draw_centered_lines() blanks this whole inner area with spaces
        # before drawing its own text (see its own docstring — the wide-
        # character corruption fix) — call it FIRST, then draw the
        # dashed outline on top, or the text wipes the outline out.
        # _resolved_target_id() doubles as the workspace's own display
        # label — sidebar.py's own rows use it exactly the same way
        # (" {ws_id} "), so no separate lookup is needed even when
        # there's no real Region object to read a name off of (the
        # never-visited case).
        draw_centered_lines(stdscr, (x, y, w, h), [(f"WS {_resolved_target_id(ctx)}", dim), ("empty *crickets*", dim)])
        _draw_dashed_outline(stdscr, y + 1, x + 1, h - 2, w - 2, dim)
        return

    # Tiled windows are laid out together, in one pass — the only way
    # to guarantee every pair of adjacent siblings gets exactly one
    # gap cell between them, see _layout_tiled_windows()'s own
    # docstring. Floating windows stay fully independent (deliberately
    # allowed to overlap tiled ones, see this module's own top
    # docstring), so they keep using _window_screen_rect() per window.
    tiled_rects, too_small_groups, tab_group_bars, group_frames, group_active_ids = _layout_tiled_windows(tiled, x, y, w, h, ctx.config)

    # One outer outline PER stacked/tabbed group, spanning its whole
    # allocated cell (bars + content box together) — the plain border
    # color, same as an ordinary UNselected window (accent is reserved
    # for floating windows only, per Rafi's own live call, 2026-08-31 —
    # reusing it here would make cyan mean two unrelated things). Makes
    # "these all belong to one group" legible without needing a
    # caption; the ACTIVE member (row + content box, drawn below) is
    # what actually stands out, in white — see _place_tab_group()'s and
    # _draw_tab_bar()'s own docstrings.
    for gx, gy, gw, gh in group_frames:
        draw_box_outline(stdscr, gy, gx, gh, gw, theme.get("border", 0))

    tiled_by_id = {window.id: window for window in tiled}
    for window_id, (win_x, win_y, win_w, win_h) in tiled_rects.items():
        window = tiled_by_id[window_id]
        is_selected = f"preview:{window.id}" == ctx.selected_id
        if is_selected:
            border_color = theme.get("selected", 0)
        elif window_id in group_active_ids:
            border_color = theme.get("border_selected", 0)
        else:
            border_color = theme.get("border", 0)
        _draw_window(stdscr, window, win_x, win_y, win_w, win_h, border_color, theme.get("text", 0), ctx.config)

    # A stacked/tabbed group's own member rows/tabs — see
    # _place_tab_group()'s own docstring for which members get one.
    for member, (bar_x, bar_y, bar_w, bar_h), is_active, corner_label in tab_group_bars:
        is_selected = f"preview:{member.id}" == ctx.selected_id
        _draw_tab_bar(stdscr, member, bar_x, bar_y, bar_w, bar_h, is_active, is_selected, corner_label, theme, ctx.config)

    for window in floating:
        is_selected = f"preview:{window.id}" == ctx.selected_id
        color = theme.get("selected", 0) if is_selected else theme.get("accent", 0)
        win_x, win_y, win_w, win_h = _window_screen_rect(window, x, y, w, h)
        if _detail_tier(win_w, win_h) == "none":
            # No sibling-tree scale to match for a floating window (see
            # this module's own top docstring) — its own independent
            # rect is the group, same as any other single-window group.
            too_small_groups.append((win_x, win_y, win_w, win_h, [window]))
        else:
            _draw_window(stdscr, window, win_x, win_y, win_w, win_h, color, color, ctx.config, filled=True)

    # Every too-small window (see _detail_tier()'s own docstring) still
    # HAS a real nav target — it just didn't get its own drawn box, a
    # crammed handful of overlapping tags reading as noise, not detail.
    # ONE dashed placeholder PER COLLAPSED GROUP (same helper the
    # empty-workspace placeholder already uses), each already sized to
    # match its own real allocated cell — see _layout_tiled_windows()'s
    # own docstring for why that's the whole point of collapsing at the
    # CELL level instead of after the fact: a group's box visibly
    # matches the scale of whatever real sibling sits next to it,
    # rather than shrink-wrapping just its own (much tinier)
    # individual windows' post-split rects. Leaving them as unexplained
    # blank space instead (no placeholder at all) read as a rendering
    # bug the first time it was seen live, not "intentionally
    # condensed".
    if too_small_groups:
        urgent_color = theme.get("urgent", 0)
        for gx, gy, gw, gh, _members in too_small_groups:
            _draw_dashed_outline(stdscr, gy, gx, gh, gw, urgent_color)

        # Bottom-right of the WHOLE outer preview box, near its own
        # corner marks — one aggregate count, not one label per group.
        total_count = sum(len(members) for _gx, _gy, _gw, _gh, members in too_small_groups)
        message = f"+{total_count} window{'s' if total_count != 1 else ''}, too small"
        try:
            stdscr.addstr(y + h - 1, x + w - display_width(message) - 2, message, urgent_color)
        except curses.error:
            pass


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


def _corner_label(window, cfg=None):
    """Pure logic: the compact per-corner identifier — the app_id's
    own first letter, uppercased, in brackets ("[K]" for kitty, "[F]"
    for firefox, "[C]" for code). Small enough to survive even a tight
    overlap between two windows' corners, where the full label (see
    _window_label(), drawn once, centered) would get lost entirely.
    Falls back to "[?]" for the pathological case of an app_id with no
    characters at all — a real WM shouldn't ever report that, but
    label drawing shouldn't crash if one somehow did.

    `cfg` is optional (existing callers that only ever draw ONE window
    at this size don't need it) — when given, and condense_title()
    finds a real, distinct detail (same source _window_label() itself
    uses), its own first letter is appended lowercase: "[K-i]" for a
    kitty running impala, "[K-h]" for one running htop. Found live,
    GitHub issue #8's own stacked/tabbed groups (2026-08-31): several
    kitty windows crammed into one narrow tab strip all read as an
    identical "[K]", useless for telling them apart — this is the
    per-window half of that fix. The other half, disambiguating
    against real SIBLINGS when this still collides (several bare
    shells with no distinguishing detail at all), is
    _group_corner_labels()' own job, not this function's — this one
    only ever looks at its own window.
    """
    app_id = window.app_id or ""
    letter = app_id[0].upper() if app_id else "?"
    if cfg is not None:
        detail = condense_title(window.app_id, window.title, cfg)
        if detail and detail.lower() != window.app_id.lower():
            return f"[{letter}-{detail[0].lower()}]"
    return f"[{letter}]"


def _group_corner_labels(members, cfg):
    """Pure logic: one short corner label per group member (see
    _corner_label()'s own docstring for the base "[X]"/"[X-y]" shape),
    disambiguated against its OWN group siblings ONLY — not the whole
    preview (Rafi's own scoping call, 2026-08-31: numbering a kitty "3"
    when its actual position/context is a totally different part of
    the screen would read as arbitrary, not helpful). Each member's
    own preferred label is computed first via _corner_label(); only
    labels that COLLIDE with a sibling's (whether both are plain "[K]"
    with no distinguishing detail, or both happen to reduce to the
    same "[K-h]") get replaced with a numbered "[X-1]"/"[X-2]"/... in
    member order instead — a member whose own detail letter already
    tells it apart from every sibling keeps that more useful label,
    it's only forced to a bare number when even that doesn't help.
    """
    preferred = {m.id: _corner_label(m, cfg) for m in members}
    counts = {}
    for label in preferred.values():
        counts[label] = counts.get(label, 0) + 1

    labels = {}
    seen_per_letter = {}
    for m in members:
        label = preferred[m.id]
        if counts[label] > 1:
            app_id = m.app_id or ""
            letter = app_id[0].upper() if app_id else "?"
            seen_per_letter[letter] = seen_per_letter.get(letter, 0) + 1
            label = f"[{letter}-{seen_per_letter[letter]}]"
        labels[m.id] = label
    return labels


class _TabGroupUnit:
    """One partition-tree leaf standing in for a whole stacked/tabbed
    container's group of windows (GitHub issue #8) — _group_tiled_windows()
    is the only thing that constructs these, see its own docstring for
    why _partition_windows() needs exactly one shared-rect unit per
    group instead of one leaf per member. Duck-types as far as
    _partition_windows()/_node_extent() are concerned (both only ever
    touch `.rect`); _subtree_windows() and _layout_tiled_windows()'s own
    walk() are the two places that know about this type specifically,
    since they're the ones that have to turn "one shared footprint"
    back into "N real, individually selectable windows".

    `slots` — not `members` — is the group's real, WM-level membership:
    a list of list[Window], one entry per direct child of the real
    stacked/tabbed container (one bar/tab row each), NOT one entry per
    leaf window. A slot usually holds exactly one window, but can hold
    several (Rafi's own "preview must show reality" call, GitHub issue
    #8 follow-up, 2026-08-31: splitting a new window open while a
    stack's own active slot is focused puts it side by side WITHIN
    that slot, not as a new top-level member — see
    tab_groups.tab_info_by_leaf_id()'s own tab_slot_id docstring for
    the full mechanism). _place_tab_group() is what actually renders
    each slot as one row/tab, recursing into _layout_tiled_windows()
    itself for any slot with more than one window.
    """

    def __init__(self, group_id, layout):
        self.group_id = group_id
        self.layout = layout
        self.slots = []
        self.rect = None


def _group_tiled_windows(windows):
    """Collapse windows sharing a tab_group_id into one _TabGroupUnit
    each (in first-occurrence order), so _partition_windows() sees one
    leaf per real on-screen footprint instead of N leaves at the exact
    same rect. Real sway/i3 data reports every stacked/tabbed group
    member at an IDENTICAL rect (only one is actually visible at a
    time — see model.py's own Window.tab_group_id docstring) — fed to
    _partition_windows() unchanged, that breaks its whole guillotine-
    cut premise: with no rect difference to find a cut along, the
    group's own members never collapse into a single node, and each
    ends up placed independently on top of the others (the original,
    literal "overlapping windows" bug this whole mechanism exists to
    fix).

    A group with only ONE real SLOT (not one window — see
    _TabGroupUnit's own docstring for why those differ; several
    windows sharing one slot still count as one here) is deliberately
    passed through as plain, individually-partitioned windows instead
    of wrapped — there's no second SLOT to distinguish it from, so the
    special stacked/tabbed rendering would just be a single full-
    detail box (or, for a multi-window lone slot, an ordinary split)
    with extra steps. Passing each window through individually like
    this is safe even when that lone slot holds more than one window:
    unlike genuine stacked/tabbed siblings (which share an identical
    rect, the whole reason this file exists), real split siblings
    always have DISTINCT rects, so ordinary guillotine-cut
    partitioning already handles them correctly with no help needed.
    """
    # getattr(..., None), not window.tab_group_id directly — several
    # existing pure-layout tests (test_preview.py) exercise this whole
    # module's geometry math against minimal SimpleNamespace(id, rect)
    # fixtures that predate tab_group_id and have no reason to grow it
    # just to keep working; a window genuinely missing this field is
    # just never grouped, same as a real ungrouped Window.
    slot_windows = {}
    slot_order = {}
    for window in windows:
        gid = getattr(window, "tab_group_id", None)
        if gid is None:
            continue
        sid = getattr(window, "tab_slot_id", None)
        key = (gid, sid)
        if key not in slot_windows:
            slot_windows[key] = []
            slot_order.setdefault(gid, []).append(sid)
        slot_windows[key].append(window)

    real_groups = {gid for gid, sids in slot_order.items() if len(sids) >= 2}

    units = []
    group_units = {}
    for window in windows:
        gid = getattr(window, "tab_group_id", None)
        if gid not in real_groups:
            units.append(window)
            continue
        if gid in group_units:
            continue  # this window's own slot is already inside the unit built below
        unit = _TabGroupUnit(gid, window.tab_group_layout)
        unit.rect = window.rect  # identical across every real slot
        unit.slots = [slot_windows[(gid, sid)] for sid in slot_order[gid]]
        group_units[gid] = unit
        units.append(unit)

    return units


def _place_slot_content(slot, cx, cy, cw, ch, result, tab_group_bars, cfg, too_small_groups, group_frames, group_active_ids):
    """Pure logic: give one stacked/tabbed group's ACTIVE slot its real
    content placement — the slot's own list of windows (see
    _TabGroupUnit's own docstring for why a slot can hold more than
    one), not just a single one.

    The overwhelmingly common case (exactly one window in the slot)
    is unchanged: one entry in `result`, same as any ordinary window.

    A slot with MORE than one window (GitHub issue #8 follow-up, found
    live 2026-08-31 — Rafi's own "preview must show reality" call: a
    stack's active slot can itself be a real, multi-window split, e.g.
    splitting a new terminal open right next to an editor while that
    editor's own stack slot is focused) recurses into
    _layout_tiled_windows() itself, on just this slot's own windows,
    confined to this content area — rendered exactly like any other
    ordinary tiled split anywhere else in the preview, not flattened
    into phantom extra stack rows. Every accumulator it produces
    (further too-small groups, even a FURTHER nested stacked/tabbed
    group, in principle) merges straight into this call's own — the
    recursion is genuinely general, not special-cased to "exactly 2".

    The -1/+2 coordinate trick cancels out _layout_tiled_windows()'s
    own assumption that it's filling a bordered box with a 1-cell
    margin reserved for that border — this content area has no border
    of its own to draw (the group's outer frame already provides one,
    see draw()'s own group_frames loop), so the nested call needs to
    use the FULL cx/cy/cw/ch as real drawable space, not shave a
    margin off it a second time.

    A slot with no room at all (cw/ch <= 0) is left entirely to
    _layout_tiled_windows()'s own end-of-function safety net — every
    window in it still gets SOME placement, just not from here.
    """
    if ch <= 0 or cw <= 0:
        return
    if len(slot) == 1:
        result[slot[0].id] = (cx, cy, cw, ch)
        return
    nested = _layout_tiled_windows(slot, cx - 1, cy - 1, cw + 2, ch + 2, cfg)
    nested_result, nested_too_small, nested_bars, nested_frames, nested_active = nested
    result.update(nested_result)
    too_small_groups.extend(nested_too_small)
    tab_group_bars.extend(nested_bars)
    group_frames.extend(nested_frames)
    group_active_ids.update(nested_active)


def _place_tab_group(unit, win_x, win_y, win_w, win_h, result, tab_group_bars, cfg, too_small_groups, group_frames, group_active_ids):
    """Pure logic: turn one _TabGroupUnit's already-allocated screen
    cell into real per-slot placements — the piece that actually gives
    the two agreed-on visual styles their shape (confirmed with Rafi
    live, not an assumption): "stacked" gets a vertical list of thin
    one-row title bars (mirroring sway's own real stacked look: every
    slot's bar is always visible, stacked top to bottom); "tabbed"
    gets one thin horizontal strip split into per-slot segments
    (mirroring sway's own tab strip). Both are deliberately THIN, not
    full-height/full-width — filling the whole cell the way an
    ordinary tiled window box does would make a group visually
    indistinguishable from a plain splitv/splith, defeating the whole
    point of a dedicated stacked/tabbed treatment.

    Both layouts show EVERY slot — the active one included — in the
    list/strip (matched live, 2026-08-31): the whole membership/order
    is visible at a glance, one row/tab per slot using that slot's own
    FIRST window as its representative for the label (see
    _group_corner_labels()' own docstring — a hidden, inactive slot
    that happens to itself hold more than one window is a real but
    rare case, and only gets this one representative's name; the
    ACTIVE slot's own real content, by contrast, is never simplified
    this way — see _place_slot_content()'s own docstring). The active
    slot ALSO still gets its normal content placement, same as any
    other window(s), written into `result` so draw()'s existing
    tiled_rects loop renders it with no special-casing. Yes, that
    means the active slot's own info is shown twice right now (once as
    a row/tab, once as the content) — an accepted, deliberate
    duplication for now while this is still being tuned live, not a
    bug.

    `cfg` is threaded all the way down from draw()/nav_items(), partly
    to reach _group_corner_labels() below and partly to hand onward to
    _place_slot_content()'s own possible recursive
    _layout_tiled_windows() call.

    Degenerate case (cell too short/narrow to fit every bar, or even
    to leave the active slot any content room): bars pile onto
    whatever row/column is still available rather than being dropped —
    every slot always ends up with SOME real, distinct-enough nav
    target, never silently missing one. Known, accepted imprecision
    for a case _detail_tier() mostly filters out already (a group's
    own allocated cell has to be at least "letter"-tier sized to reach
    this function at all).

    Returns the active slot's own representative window id WHEN that
    slot is a single window, else None — _layout_tiled_windows() uses
    a non-None return to build group_active_ids, so draw() knows to
    color that one window's content box distinctly (white,
    border_selected — not accent, which stays reserved for floating
    windows only). A multi-window active slot deliberately gets no
    such special coloring on any of its own windows — see
    _place_slot_content()'s own docstring: they're rendered as an
    ordinary nested split, no single one of them is "the" active
    window the way a lone one would be.
    """
    active_slot = next((slot for slot in unit.slots if slot[0].tab_active), unit.slots[0])
    representatives = [slot[0] for slot in unit.slots]
    labels = _group_corner_labels(representatives, cfg)
    active_id = active_slot[0].id if len(active_slot) == 1 else None

    if unit.layout == "tabbed":
        n = len(unit.slots)
        strip_h = 1 if n > 0 and win_h >= 1 else 0
        content_h = max(win_h - strip_h, 0)
        if strip_h:
            widths = _allocate_axis([1.0] * n, win_w, gap=1)
            offset = 0
            for slot, width in zip(unit.slots, widths):
                rep = slot[0]
                tab_group_bars.append((rep, (win_x + offset, win_y, width, 1), slot is active_slot, labels[rep.id]))
                offset += width + 1
        _place_slot_content(
            active_slot, win_x, win_y + strip_h, win_w, content_h,
            result, tab_group_bars, cfg, too_small_groups, group_frames, group_active_ids,
        )
        return active_id

    # "stacked": one contiguous row per slot (active included), top to
    # bottom, gap=0 — real sway stacked title bars sit flush against
    # each other, their own border lines are the only separation needed
    # (same reasoning _allocate_axis()'s own docstring gives for why
    # row-splits use gap=0 elsewhere in this file).
    n = len(unit.slots)
    bars_h = min(n, win_h) if win_h > 0 else 0
    content_h = max(win_h - bars_h, 0)
    for i, slot in enumerate(unit.slots):
        row = min(i, bars_h - 1) if bars_h > 0 else 0
        rep = slot[0]
        tab_group_bars.append((rep, (win_x, win_y + row, win_w, 1), slot is active_slot, labels[rep.id]))
    _place_slot_content(
        active_slot, win_x, win_y + bars_h, win_w, content_h,
        result, tab_group_bars, cfg, too_small_groups, group_frames, group_active_ids,
    )
    return active_id


def _partition_windows(windows, x0, x1, y0, y1):
    """Pure logic: reconstruct the recursive guillotine-cut split tree
    a set of tiled windows must have come from, using nothing but each
    window's own flat rect (window.rect — a 0..1 fraction of its
    region; model.py's Window carries no parent/sibling structure from
    the real WM tree at all, so this is genuinely all there is to work
    from). Sway/i3 tiling is ALWAYS a binary space partition — every
    split is a plain left/right or top/bottom cut, never a "pinwheel"
    arrangement — so for any real set of tiled-window rects, there is
    always SOME single axis-aligned line that cleanly separates them
    into two non-straddling groups; recursing on each side is exact,
    not a heuristic, for genuine WM data.

    `windows` may contain plain Window objects, _TabGroupUnit objects
    (see _group_tiled_windows()), or a mix of both — this function only
    ever touches `.rect`, so it doesn't need to know the difference.

    Returns a tree of plain tuples:
      ("leaf", window)                        — one window
      ("split", "x"|"y", [child, child, ...])  — a real guillotine cut
      ("unsplit", windows, x0, x1, y0, y1)     — fallback: no clean cut
        found among these windows at all (should never happen for real
        sway/i3 data; kept only so a future bug elsewhere degrades to
        "each window placed independently" instead of crashing).

    x0/x1/y0/y1 are search bounds (0..1, in the ORIGINAL whole-region
    coordinate space) for the recursive call, not carried in "leaf"/
    "split" nodes — a leaf is exactly its own window regardless of
    where the search happened to narrow to, and a split's own bounds
    are implicit in its children's.
    """
    if len(windows) == 1:
        return ("leaf", windows[0])

    eps = 1e-4
    xs = sorted({w.rect[0] for w in windows} | {w.rect[0] + w.rect[2] for w in windows})
    for xc in xs:
        if xc <= x0 + eps or xc >= x1 - eps:
            continue
        left = [w for w in windows if w.rect[0] + w.rect[2] <= xc + eps]
        right = [w for w in windows if w.rect[0] >= xc - eps]
        if left and right and len(left) + len(right) == len(windows):
            return ("split", "x", [
                _partition_windows(left, x0, xc, y0, y1),
                _partition_windows(right, xc, x1, y0, y1),
            ])

    ys = sorted({w.rect[1] for w in windows} | {w.rect[1] + w.rect[3] for w in windows})
    for yc in ys:
        if yc <= y0 + eps or yc >= y1 - eps:
            continue
        top = [w for w in windows if w.rect[1] + w.rect[3] <= yc + eps]
        bottom = [w for w in windows if w.rect[1] >= yc - eps]
        if top and bottom and len(top) + len(bottom) == len(windows):
            return ("split", "y", [
                _partition_windows(top, x0, x1, y0, yc),
                _partition_windows(bottom, x0, x1, yc, y1),
            ])

    return ("unsplit", windows, x0, x1, y0, y1)


def _node_extent(node, axis):
    """Pure logic: how much of the given axis (0..1, whole-region
    fraction) a partition-tree node spans — a leaf's own rw/rh, a
    split ALONG this axis sums its children's extents (they cover the
    combined span sequentially), and a split along the OTHER axis just
    reads its first child's extent (every child of a cut along the
    other axis spans the same range on THIS one — the guillotine
    invariant _partition_windows() itself relies on to find that cut
    in the first place).
    """
    idx = 2 if axis == "x" else 3
    kind = node[0]
    if kind == "leaf":
        return node[1].rect[idx]
    if kind == "unsplit":
        x0, x1, y0, y1 = node[2], node[3], node[4], node[5]
        return (x1 - x0) if axis == "x" else (y1 - y0)
    _, split_axis, children = node
    if split_axis == axis:
        return sum(_node_extent(c, axis) for c in children)
    return _node_extent(children[0], axis)


def _allocate_axis(sizes, total_cells, gap=1):
    """Pure logic: divide total_cells terminal cells among len(sizes)
    siblings, proportionally to their real relative sizes, while
    reserving EXACTLY `gap` cells between each pair of neighbors —
    never LESS than that (gap=1's own zero-cell case is the original
    "windows glued together" bug) and never more either (found live: a
    first fix guaranteed "at least one" by shaving each window's own
    width independently, which left visibly DIFFERENT gap widths
    across different splits of the very same real layout, since
    leftover rounding slack isn't reserved anywhere in particular — it
    just piles up whichever window's own rounding happened to go up).
    Largest-remainder rounding: every size gets its rounded-down
    proportional share first, then whichever siblings lost the most to
    flooring get the few cells still left over, one each — the
    allocations always sum to EXACTLY total_cells - (n-1)*gap, never
    drifting over or under.

    gap defaults to 1, but _layout_tiled_windows() calls this with
    gap=0 for row-splits (y axis) specifically — found live, a real
    terminal character cell is taller than it is wide (fonts commonly
    run something like 1:2 width:height), so a "1 row" gap reads as
    visibly THICKER on screen than a "1 column" gap even though both
    are "1 cell" in the abstract grid; two windows' own border LINES
    (top window's bottom border, bottom window's own top border) already
    read as clearly separate rows on their own, with no blank row
    needed between them the way a blank COLUMN is needed between two
    side-by-side windows' vertical border lines.

    Degrades to 1 cell each (ignoring real proportions, but still
    never letting two windows touch when gap > 0) when there isn't
    even room for that — a curses.error guard at the real draw call
    site already tolerates writing past the box's own edge in that
    case, same tolerance every other degenerate-box case in this file
    leans on.
    """
    n = len(sizes)
    if n == 1:
        return [max(total_cells, 1)]
    available = total_cells - (n - 1) * gap
    if available < n:
        return [1] * n
    total = sum(sizes) or 1.0
    raw = [s / total * available for s in sizes]
    floors = [int(r) for r in raw]
    remainder = available - sum(floors)
    order = sorted(range(n), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def _tab_group_unit_windows(unit):
    """Every real window a _TabGroupUnit stands in for, flattened out
    of its slots (list of list[Window] — see _TabGroupUnit's own
    docstring for why it's not a flat list already)."""
    return [w for slot in unit.slots for w in slot]


def _subtree_windows(node):
    """Pure logic: every real window under a partition-tree node, in
    tree order — used to know which window ids a collapsed subtree
    (see _layout_tiled_windows()'s own "too small" handling) actually
    stands in for. A _TabGroupUnit leaf/unsplit-member is flattened
    into its real member windows here — a collapsed too-small
    placeholder needs the actual window count/ids underneath it, not
    the one synthetic unit standing in for them during partitioning.
    """
    kind = node[0]
    if kind == "leaf":
        payload = node[1]
        return _tab_group_unit_windows(payload) if isinstance(payload, _TabGroupUnit) else [payload]
    if kind == "unsplit":
        flat = []
        for item in node[1]:
            flat.extend(_tab_group_unit_windows(item) if isinstance(item, _TabGroupUnit) else [item])
        return flat
    return [w for child in node[2] for w in _subtree_windows(child)]


def _layout_tiled_windows(windows, x, y, w, h, cfg=None):
    """Pure logic: lay out a region's whole set of TILED windows into
    terminal-cell sub-rects together, in one pass — the only way to
    guarantee every pair of adjacent siblings gets EXACTLY one gap
    cell between them regardless of the preview box's own size (see
    _allocate_axis()'s own docstring for why doing this per-window,
    independently, can't guarantee that). NOT used for floating
    windows — those are deliberately allowed to overlap tiled ones
    (see this module's own top docstring), so they keep using
    _window_screen_rect()'s simpler, fully independent placement.

    Returns ({window.id: (win_x, win_y, win_w, win_h)}, too_small_groups,
    tab_group_bars, group_frames, group_active_ids) — the second
    element is a list of (win_x, win_y, win_w, win_h, [window, ...])
    for every subtree collapsed as a whole (see below); windows inside
    one of those groups do NOT get an entry in the first dict at all,
    draw()'s own call site is what turns each group into one combined
    placeholder box instead of drawing them individually. The third
    element is a list of (window, (bar_x, bar_y, bar_w, bar_h),
    is_active, corner_label) — one entry per stacked/tabbed group
    member; `corner_label` is that member's own pre-disambiguated short
    label (see _group_corner_labels()' own docstring — resolved once
    per group, against its real siblings, since _draw_tab_bar() itself
    only ever sees one member at a time). The active member still gets
    a normal entry in the first dict too, same as any other window —
    see _place_tab_group()'s own docstring for why. The
    fourth element is a list of (win_x, win_y, win_w, win_h) — one per
    rendered stacked/tabbed group's OWN full allocated cell (bars +
    content box together), so draw() can outline the whole thing as
    one visual unit ("these all belong to the same group"). The fifth
    is a set of window ids — every group's own active member — so
    draw() can color that one window's content box distinctly from an
    ordinary standalone window even though both live in the same
    result dict.

    A node — leaf OR a whole split subtree — collapses into ONE group
    the moment its own ALLOCATED cell drops below the "letter" detail
    threshold (_detail_tier()), checked BEFORE recursing any further,
    not after computing each individual leaf's own (much tinier) rect
    and only THEN noticing they're all too small. This matters for
    more than just efficiency: found live, bounding just the leaves'
    own tiny post-split rects produces a placeholder box far SMALLER
    than what that whole branch actually occupies in the real tiling —
    every cell a deep spiral's later splits ate into stays outside the
    box, reading as "some random small area is too small", not "this
    whole branch, the same size as its own sibling, is". Collapsing at
    the CELL level instead means the placeholder is exactly as large
    as an un-recursed leaf in that same spot would have been — visibly
    matching scale with whatever real sibling sits next to it.
    """
    if not windows:
        return {}, [], [], [], set()

    units = _group_tiled_windows(windows)
    tree = _partition_windows(units, 0.0, 1.0, 0.0, 1.0)
    inner_w, inner_h = max(w - 2, 0), max(h - 2, 0)
    result = {}
    too_small_groups = []
    tab_group_bars = []
    group_frames = []
    group_active_ids = set()

    def walk(node, cell_x, cell_y, cell_w, cell_h):
        kind = node[0]
        win_x, win_y = x + 1 + cell_x, y + 1 + cell_y
        win_w, win_h = max(cell_w, 1), max(cell_h, 1)

        if kind != "leaf" and _detail_tier(win_w, win_h) == "none":
            too_small_groups.append((win_x, win_y, win_w, win_h, _subtree_windows(node)))
            return
        if kind == "leaf":
            payload = node[1]
            if _detail_tier(win_w, win_h) == "none":
                too_small_groups.append((win_x, win_y, win_w, win_h, _subtree_windows(node)))
                return
            if isinstance(payload, _TabGroupUnit):
                # The outer frame (drawn around the FULL win_x/y/w/h
                # below) owns this cell's own border row/column — bars
                # and the content box are inset by 1 cell into it, same
                # margin _draw_window() itself reserves for its own
                # border, so nothing ever draws ON TOP of the frame's
                # own outline (found live: without this inset, the
                # topmost bar's text collided directly with the frame's
                # own top border line).
                inset_w, inset_h = max(win_w - 2, 1), max(win_h - 2, 1)
                active_id = _place_tab_group(
                    payload, win_x + 1, win_y + 1, inset_w, inset_h, result, tab_group_bars, cfg,
                    too_small_groups, group_frames, group_active_ids,
                )
                if active_id is not None:
                    group_active_ids.add(active_id)
                group_frames.append((win_x, win_y, win_w, win_h))
                return
            result[payload.id] = (win_x, win_y, win_w, win_h)
            return
        if kind == "unsplit":
            group, gx0, gx1, gy0, gy1 = node[1], node[2], node[3], node[4], node[5]
            span_x, span_y = (gx1 - gx0) or 1.0, (gy1 - gy0) or 1.0
            for item in group:
                rx, ry, rw, rh = item.rect
                leaf_x = x + 1 + cell_x + round((rx - gx0) / span_x * cell_w)
                leaf_y = y + 1 + cell_y + round((ry - gy0) / span_y * cell_h)
                # -1 shave on x only, none on y — same asymmetry as the
                # real split path's x/y gap split, see _allocate_axis()'s
                # own docstring.
                leaf_w = max(round(rw / span_x * cell_w) - 1, 1)
                leaf_h = max(round(rh / span_y * cell_h), 1)
                if isinstance(item, _TabGroupUnit):
                    inset_w, inset_h = max(leaf_w - 2, 1), max(leaf_h - 2, 1)
                    active_id = _place_tab_group(
                        item, leaf_x + 1, leaf_y + 1, inset_w, inset_h, result, tab_group_bars, cfg,
                        too_small_groups, group_frames, group_active_ids,
                    )
                    if active_id is not None:
                        group_active_ids.add(active_id)
                    group_frames.append((leaf_x, leaf_y, leaf_w, leaf_h))
                else:
                    result[item.id] = (leaf_x, leaf_y, leaf_w, leaf_h)
            return
        _, axis, children = node
        gap = 1 if axis == "x" else 0  # see _allocate_axis()'s own docstring for why y gets 0
        sizes = [_node_extent(c, axis) for c in children]
        allocs = _allocate_axis(sizes, cell_w if axis == "x" else cell_h, gap=gap)
        offset = 0
        for child, alloc in zip(children, allocs):
            if axis == "x":
                walk(child, cell_x + offset, cell_y, alloc, cell_h)
            else:
                walk(child, cell_x, cell_y + offset, cell_w, alloc)
            offset += alloc + gap

    walk(tree, 0, 0, inner_w, inner_h)

    # Live-found, deep autotiling spirals (28+ windows on this
    # session's own real machine): _partition_windows()'s eps-based
    # boundary matching can drop a window from the tree entirely at
    # deep enough nesting (real crash confirmed live — nav_items()
    # doing tiled_rects[window.id] on a dropped id raised KeyError,
    # bringing tuicc's whole main loop down). Root cause not fully
    # nailed down (probably eps being either too big relative to
    # extremely small deeply-nested fractions, or too small relative
    # to real accumulated per-split pixel rounding — a fixed constant
    # can't be right for both), but a whole-group layout can NEVER be
    # allowed to crash the app over a cosmetic gap-consistency
    # feature — any window missing after the real walk (from either
    # result or a too_small_groups entry) is placed via the simpler,
    # always-safe per-window _window_screen_rect() instead of just
    # failing silently or raising.
    grouped_ids = {w.id for _x, _y, _w, _h, group in too_small_groups for w in group}
    bar_ids = {member.id for member, _rect, _is_active, _label in tab_group_bars}
    for window in windows:
        if window.id not in result and window.id not in grouped_ids and window.id not in bar_ids:
            result[window.id] = _window_screen_rect(window, x, y, w, h)

    return result, too_small_groups, tab_group_bars, group_frames, group_active_ids


def _window_screen_rect(window, x, y, w, h):
    """Pure logic: convert a window's real relative rect (window.rect —
    0..1 fractions of its region, from the WM) into terminal-cell
    coordinates within this preview box, fully independently of any
    sibling. Used for FLOATING windows only — genuinely, deliberately
    allowed to overlap tiled ones (see this module's own top
    docstring), so there's no "siblings" concept to reconcile against
    the way _layout_tiled_windows() has to for tiled ones. nav_items()
    also uses this for floating windows, so the box actually drawn and
    the rect Tab-navigation uses to select it always agree.

    win_w/win_h are NOT round(rw * (w-2)) directly — a lone rounding of
    the width alone doesn't guard against a floating window's own edge
    landing flush against a real pixel-thin real gap the same way
    tiled windows originally could (see CLAUDE/NOTES/known-limitations.md,
    and _layout_tiled_windows()'s own docstring for the fuller story
    of why tiled windows needed a whole-group fix instead of this
    simpler per-window one). Deriving win_w from where the window's
    own right edge (rx+rw) itself rounds to — the SAME formula used
    for win_x — rather than rounding rw on its own, then subtracting 1
    from that, guarantees a real gap cell survives whenever two
    floating windows happen to sit right at each other's edge, without
    needing to know anything about siblings at all.
    """
    rx, ry, rw, rh = window.rect
    win_x = x + 1 + round(rx * (w - 2))
    win_y = y + 1 + round(ry * (h - 2))
    right = x + 1 + round((rx + rw) * (w - 2))
    bottom = y + 1 + round((ry + rh) * (h - 2))
    win_w = max(right - win_x - 1, 1)
    win_h = max(bottom - win_y - 1, 1)
    return win_x, win_y, win_w, win_h


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


def _draw_dashed_outline(stdscr, y, x, h, w, color):
    """A dashed variant of render_utils.draw_box_outline — real corner
    glyphs (┌┐└┘, so the four corners always read as one connected box
    rather than depending on the dash phase landing on them) with
    sparse ─/│ ticks along the straight edges (one drawn cell every 3,
    not every other — found live, every-other read as "basically
    solid", not meaningfully different from draw_box_outline's own real
    line). Non-tick cells are left untouched (not overwritten with an
    explicit space) — the caller already blanked this whole area via
    draw_centered_lines() first, see the empty-workspace call site's
    own comment for why the ordering there matters. Used only by that
    placeholder; kept local rather than promoted to render_utils.py
    since there's no second consumer yet — same "share once actually
    reused, not preemptively" discipline this session's own
    render_utils.py work already applied more than once.
    """
    if h < 2 or w < 2:
        return
    try:
        stdscr.addstr(y, x, "┌", color)
        stdscr.addstr(y, x + w - 1, "┐", color)
        stdscr.addstr(y + h - 1, x, "└", color)
        stdscr.addstr(y + h - 1, x + w - 1, "┘", color)
        for i in range(1, w - 1):
            if i % 3 == 0:
                stdscr.addstr(y, x + i, "─", color)
                stdscr.addstr(y + h - 1, x + i, "─", color)
        for i in range(1, h - 1):
            if i % 3 == 0:
                stdscr.addstr(y + i, x, "│", color)
                stdscr.addstr(y + i, x + w - 1, "│", color)
    except curses.error:
        pass


_MIN_FULL_DETAIL_W = 10
_MIN_FULL_DETAIL_H = 4
_MIN_LETTER_DETAIL_W = 5
_MIN_LETTER_DETAIL_H = 3


def _detail_tier(win_w, win_h):
    """Pure logic: how much detail a window's own scaled-down box has
    genuine room for — found live, an autotiling spiral gone deep
    enough (16+ real windows, this session's own machine) shrinks
    boxes to the point where the ORIGINAL always-draw-everything
    behavior reads as pure garbage: four corner "[K]" tags AND a
    word-wrapped center label, all crammed into a handful of cells,
    overlapping each other and neighboring windows into noise, not
    "identifying detail" anymore.

    "full" — real room for both: the word-wrapped center label AND
    all 4 corner tags (today's original behavior, unchanged above
    these thresholds).
    "letter" — only room for one clean, single, centered "[K]" tag —
    corner tags dropped entirely (four of them crammed into a box
    this small just overlap each other, no better than the center
    label they'd be replacing).
    "none" — not even that: no real content (no corner tags, no center
    label — see _draw_window()'s own "none" branch for what it draws
    INSTEAD: a plain red dashed placeholder box, not nothing — a
    window silently occupying no visible space at all read as a
    rendering bug, not "intentionally condensed", once seen live).
    The caller (draw()) also tallies "none" windows into one aggregate
    "+N windows, too small" line, so the exact count is legible even
    when several of these dashed boxes overlap or sit too close to
    count individually by eye.
    """
    if win_w >= _MIN_FULL_DETAIL_W and win_h >= _MIN_FULL_DETAIL_H:
        return "full"
    if win_w >= _MIN_LETTER_DETAIL_W and win_h >= _MIN_LETTER_DETAIL_H:
        return "letter"
    return "none"


def _draw_window(stdscr, window, win_x, win_y, win_w, win_h, border_color, text_color, cfg, filled=False):
    detail = _detail_tier(win_w, win_h)
    if detail == "none":
        # Drawn as nothing HERE, deliberately — see draw()'s own call
        # site: too-small windows get ONE combined dashed placeholder
        # covering their whole shared area, not one box each (tried
        # live, one-per-window read as its own kind of clutter once
        # several of them sat close together).
        return

    if filled:
        draw_filled_box(stdscr, win_y, win_x, win_h, win_w, border_color)

    draw_box_outline(stdscr, win_y, win_x, win_h, win_w, border_color)

    if detail == "letter":
        # One clean, centered "[K]" — see _detail_tier()'s own
        # docstring for why corner tags are dropped at this size
        # rather than kept alongside it.
        label = wc_truncate(_corner_label(window, cfg), max(win_w - 2, 0))
        try:
            stdscr.addstr(win_y + win_h // 2, centered_x(win_x + 1, max(win_w - 2, 0), label), label, text_color)
        except curses.error:
            pass
        return

    # Corner labels (dimmed — identifying detail, not something that
    # should visually compete with the window's own border/selection
    # color the way full-brightness text would) survive tight overlap
    # between windows; the full label below, shown once in the center,
    # is where the real detail (what's actually running) lives.
    corner_label = wc_truncate(_corner_label(window, cfg), max(win_w - 2, 0))
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


def _draw_tab_bar(stdscr, member, win_x, win_y, win_w, win_h, is_active, is_selected, corner_label, theme, cfg):
    """Draw one stacked/tabbed group member's own thin title bar/tab —
    see _place_tab_group()'s own docstring for the design: both layouts
    now draw a row/segment for EVERY member, active included, matching
    (2026-08-31). No border, no box — just one line of
    text, which is the whole visual point: it has to read as
    unmistakably thinner/lesser than a real _draw_window() box, or a
    group stops looking any different from ordinary tiled windows.
    Falls back to `corner_label` — the caller's own pre-disambiguated
    short label (see _group_corner_labels()' own docstring, called once
    per group in _place_tab_group() — this function only ever sees one
    member at a time, it can't disambiguate against siblings itself)
    instead of the full condensed title when there's not enough width
    for that to read cleanly (12 cells is _MIN_FULL_DETAIL_W plus a
    little slack for the brackets a real "[app] detail" label commonly
    adds).

    is_active uses border_selected (white in the default theme) — NOT
    accent, which stays reserved for floating windows only (Rafi's own
    live call, 2026-08-31, after a dim-accent attempt read as
    ambiguous with that). The group's own outer frame (draw()'s
    group_frames loop) uses the plain, unselected border color instead
    — same as an ordinary unfocused window — so the active row/box is
    what actually draws the eye as "this one specifically", while the
    frame just quietly says "these belong together". Matches the
    active member's own content box below, which draw() colors the
    same white for the same reason. Inactive rows use the same plain
    border color as the frame (no extra dimming) — this whole row IS
    already the "lesser" element by virtue of being a thin, borderless
    line next to a real box, it doesn't need dimming on top of that.
    """
    if win_h < 1 or win_w < 1:
        return
    if is_selected:
        color = theme.get("selected", 0)
    elif is_active:
        color = theme.get("border_selected", 0)
    else:
        color = theme.get("border", 0)
    label_source = _window_label(member, cfg) if win_w >= 12 else corner_label
    label = wc_truncate(label_source, win_w)
    try:
        stdscr.addstr(win_y, centered_x(win_x, win_w, label), label, color)
    except curses.error:
        pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box

    focused_region = _focused_region(ctx)

    if focused_region is None:
        return []

    # Same tiled/floating split as draw() — tiled windows' rects come
    # from the one shared whole-group layout (so Tab-navigation always
    # highlights exactly the box actually drawn), floating windows
    # stay independent.
    tiled = [win for win in focused_region.windows if not win.floating]
    tiled_rects, too_small_groups, tab_group_bars, _frames, _active_ids = _layout_tiled_windows(tiled, x, y, w, h)
    # Every window inside a collapsed group (see _layout_tiled_windows()'s
    # own docstring) shares that ONE group's rect for navigation too —
    # there's only the one placeholder box actually drawn for all of
    # them, so Tab-selecting any window in the group highlights it.
    grouped_rects = {w.id: (gx, gy, gw, gh) for gx, gy, gw, gh, members in too_small_groups for w in members}
    # A stacked/tabbed group's own members each get a thin bar/tab rect
    # here too (see _place_tab_group()'s own docstring) — but the
    # `elif window.id in tiled_rects` check below takes priority for
    # the active member, who already has a normal content-box entry
    # there, same as any other window; this dict only ends up used for
    # its OTHER (non-active) members.
    bar_rects = {member.id: rect for member, rect, _is_active, _label in tab_group_bars}

    items = []
    for window in focused_region.windows:
        if window.floating:
            win_x, win_y, win_w, win_h = _window_screen_rect(window, x, y, w, h)
            if window.id in grouped_rects:
                win_x, win_y, win_w, win_h = grouped_rects[window.id]
        elif window.id in tiled_rects:
            win_x, win_y, win_w, win_h = tiled_rects[window.id]
        elif window.id in bar_rects:
            win_x, win_y, win_w, win_h = bar_rects[window.id]
        else:
            win_x, win_y, win_w, win_h = grouped_rects[window.id]

        items.append(NavItem(
            id=f"preview:{window.id}",
            rect=(win_x, win_y, win_w, win_h),
            focus_target=window.id,
            target_kind="window",
        ))

    items.sort(key=lambda item: item.rect[0])

    return items
