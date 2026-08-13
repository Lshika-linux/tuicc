"""Media module: now-playing (one row per detected MPRIS player) +
transport controls + output switching (reuses audio/'s
set_default_sink — volume itself stays modules/control.py's concern,
a system property, not a player one).

Now-playing rows use the same two-level browsing/expanded model
sessions.py established; both sections render a fixed VISIBLE_SLOTS
window regardless of how much real content exists. See
CLAUDE/NOTES/design-decisions.md#media-module-layout for why (tab_order
interaction, stale-expansion handling, fixed-slot rationale) and
#media-cava-and-source-label for the inline visualizer placement and
_source_label()'s domain/desktop_entry/identity priority.

---
IMPORTANT: Each module owns both how it draws itself and where its own
focusable items are — the core never guesses a module's internal
layout.
"""

import curses
import time
from urllib.parse import urlparse

from tuicc.media.cava import ASCII_MAX_RANGE
from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, eighth_block_level
from tuicc.windowed_list import section_nav_indices as _section_nav_indices
from tuicc.windowed_list import section_rows as _section_rows
from tuicc.windowed_list import header_with_count as _header_with_count

MARQUEE_STEP_SECONDS = 0.3

# Redraw cadence while the cava visualizer is actively streaming —
# matched to cava.conf's own `framerate = 30` (see cava.py). Redrawing
# slower than cava produces frames reads as visibly choppy even when
# the reader thread itself keeps up fine, since a still-fresh frame
# just sits unread until the next redraw.
CAVA_REDRAW_SECONDS = 1 / 30

# Index 0 = blank (level 0), index 8 = full block (level 8) — 9 entries
# for one physical row's own 0..8 slice of a bar (see _cava_row_level()
# below). _CAVA_BLOCKS[1] (the thinnest non-blank glyph) doubles as the
# idle-state flatline character.
_CAVA_BLOCKS = " ▁▂▃▄▅▆▇█"

# Drawn inline to the right of the Output section's own rows, not as
# separate rows below it — see CLAUDE/NOTES/design-decisions.md
# #media-cava-and-source-label. Matches CavaReader's own default bar
# count 1:1 (see cava.py) — no clip/pad mismatch by construction.
CAVA_VIS_WIDTH = 24
CAVA_RIGHT_GAP = 1  # same "don't render flush against the border" margin Now Playing's controls use


def _cava_row_level(raw_height: int, row_idx: int, num_rows: int) -> int:
    """One bar's height (0..ASCII_MAX_RANGE) as seen from just ONE
    physical output row — the visualizer's total height is dynamic (one
    row per connected sink), so this scales cava's fixed range down to
    whatever 0..(num_rows*8) resolution is available this frame. Thin
    wrapper over render_utils.eighth_block_level (shared with
    modules/bars.py); kept here so existing cava-specific tests keep
    reading this name unchanged.
    """
    return eighth_block_level(raw_height, ASCII_MAX_RANGE, row_idx, num_rows)

_expanded_bus_name = None  # str | None — which player's actions are showing, if any


def _pending_blink_style(theme):
    """Same idiom as connectivity.py's/control.py's own copies —
    duplicated rather than imported, each module stays self-contained.
    """
    blink_on = int(time.time() * 2) % 2 == 0
    if blink_on:
        return theme.get("selected", 0), curses.A_BOLD
    return theme.get("text", 0), curses.A_DIM


def _source_label(player) -> str:
    """The short "where is this playing from" tag: a browser's current
    domain (e.g. "youtube.com") when a real URL is available, else the
    app's DesktopEntry, else Identity as the last resort. See
    CLAUDE/NOTES/design-decisions.md#media-cava-and-source-label for the
    priority order's reasoning and the Spotify-own-web-player exception.
    """
    if player.url:
        netloc = urlparse(player.url).netloc
        if netloc:
            domain = netloc.removeprefix("www.")
            own_domain = player.desktop_entry and player.desktop_entry.lower() in domain.lower()
            if own_domain:
                return player.identity or player.desktop_entry
            return domain
    return player.desktop_entry or player.identity


def _body_label(player) -> str:
    """The artist/title part alone, without the "[source]" prefix —
    split out from _player_label so draw() can keep the source prefix
    fixed/always-visible and marquee-scroll only this part. Scrolling
    the whole "[domain] artist - title" string as one blob would scroll
    the source tag out of view too, defeating "always visible".
    """
    if player.artist and player.title:
        return f"{player.artist} - {player.title}"
    if player.title:
        return player.title
    return player.identity


def _player_label(player) -> str:
    """The whole single-string label (source prefix + body) — kept as
    a convenience for callers that don't need draw()'s own prefix/body
    split (nothing in this module uses it that way anymore, but it's a
    reasonable, still-tested utility on its own).
    """
    body = _body_label(player)
    if body == player.identity and not player.title:
        # Nothing else worth saying beyond which app/site this is —
        # showing it again in brackets right after would just repeat
        # itself ("[Mozilla firefox] Mozilla firefox").
        return body
    source = _source_label(player)
    return f"[{source}] {body}" if source else body


def marquee_text(text: str, width: int, now: float) -> str:
    """text as-is if it already fits width. Otherwise a width-wide
    sliding window into "text + gap" repeated, advancing one character
    every MARQUEE_STEP_SECONDS of wall-clock time — same "no per-frame
    state needed, time.time() already is the shared clock" reasoning
    _pending_blink_style uses, just taking `now` as an explicit
    parameter instead of calling time.time() internally, so this stays
    unit-testable without a real clock (draw() passes time.time() in).
    """
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    gap = "   "
    looped = text + gap
    offset = int(now / MARQUEE_STEP_SECONDS) % len(looped)
    doubled = looped + looped  # a width-wide slice never runs off the end
    return doubled[offset:offset + width]


# A rough "this body text probably won't fit a typical box, so it's
# probably marquee-scrolling somewhere" length threshold — main.py uses
# this to decide whether to redraw at MARQUEE_STEP_SECONDS cadence
# instead of the idle 1s default (without it, marquee_text()'s own
# per-0.3s-step math is correct but never actually gets redrawn that
# often). Deliberately not box-width-aware: main.py's timeout decision
# happens before boxes are computed for this frame, so an exact fit
# check isn't available yet; a conservative length guess costs nothing
# worse than a few unnecessary 0.3s redraws for a body that would have
# fit anyway.
MARQUEE_LENGTH_THRESHOLD = 18


def has_scrolling_content(players: list | None) -> bool:
    return any(len(_body_label(p)) > MARQUEE_LENGTH_THRESHOLD for p in (players or []))


def is_expanded() -> bool:
    return _expanded_bus_name is not None


def collapse() -> str | None:
    """Leaves the expanded (level 2) state, back to browsing every
    player's single row — main.py calls this on Escape while a player
    is expanded, or when active_module leaves "media" entirely. Returns
    the bus_name that WAS expanded (None if none was), so the caller
    can reselect "media:<bus_name>:row" directly — same reason
    sessions.py's collapse() returns the slot it collapsed (see its own
    docstring): nav_items() no longer reports the just-selected action
    id the instant this collapses, which would otherwise trip main.py's
    stale-selection recovery into jumping to the sidebar.
    """
    global _expanded_bus_name
    bus_name = _expanded_bus_name
    _expanded_bus_name = None
    return bus_name


def _reconcile_expanded_state(players: list) -> None:
    """Clears _expanded_bus_name if that player has disappeared from
    the current snapshot (quit, or a transient poll gap) — without
    this, is_expanded() would keep reporting True forever for a player
    that no longer exists anywhere in nav_items()' own output, since
    nothing else ever resets it except leaving the module or pressing
    Escape.
    """
    global _expanded_bus_name
    if _expanded_bus_name is None:
        return
    if not any(p.bus_name == _expanded_bus_name for p in players):
        _expanded_bus_name = None


# Each section (Now Playing's players, Output's sinks) always renders
# exactly VISIBLE_SLOTS rows — never more (a scroll window kicks in,
# see windowed_list.window_start), never fewer (unfilled slots show an
# "[empty - ...]" placeholder, see windowed_list.section_rows). See
# CLAUDE/NOTES/design-decisions.md#media-module-layout; the mechanism
# now lives in windowed_list.py, shared with modules/sysmon.py.


def _selected_player_index(players: list, selected_id: str | None, expanded_bus_name: str | None) -> int | None:
    """Which index into `players` the current selection corresponds to,
    if any — used to anchor _window_start so navigating to (or
    expanding) a player keeps its row in view. Checks expanded_bus_name
    FIRST: an expanded row's own transport-control ids
    ("media:<bus_name>:prev"/"playpause"/"next") still embed the same
    bus_name, but checking the expansion directly is more direct than
    re-parsing one of those three id shapes.
    """
    target_bus_name = expanded_bus_name
    if target_bus_name is None and selected_id and selected_id.startswith("media:") \
            and not selected_id.startswith("media:output:"):
        target_bus_name = selected_id.split(":")[1]
    if target_bus_name is None:
        return None
    for i, player in enumerate(players):
        if player.bus_name == target_bus_name:
            return i
    return None


def _selected_output_index(sinks: list, selected_id: str | None) -> int | None:
    if not selected_id or not selected_id.startswith("media:output:"):
        return None
    sink_id = selected_id.split(":", 2)[2]
    for i, sink in enumerate(sinks):
        if sink.id == sink_id:
            return i
    return None


def _build_rows(ctx, box_h):
    """One row per line the box renders, in order — the single source
    of truth draw() and nav_items() both walk, same reasoning
    connectivity.py's own _build_rows gives for why this can't drift.
    """
    players = ctx.status.get("media") if ctx.status is not None else None
    media_error = ctx.status.get_error("media") if ctx.status is not None else None
    sinks = ctx.status.get("audio") if ctx.status is not None else None
    audio_error = ctx.status.get_error("audio") if ctx.status is not None else None

    _reconcile_expanded_state(players or [])

    selected_player_index = _selected_player_index(players or [], ctx.selected_id, _expanded_bus_name)
    selected_output_index = _selected_output_index(sinks or [], ctx.selected_id)

    visible_slots = ctx.config.media_visible_slots

    rows = [("header", _header_with_count("Now Playing", players))]
    rows.extend(_section_rows(players, media_error, selected_player_index, "player", "player",
                               visible_slots=visible_slots))

    rows.append(("spacer", None))
    rows.append(("header", _header_with_count("Output", sinks)))
    rows.extend(_section_rows(sinks, audio_error, selected_output_index, "output_item", "output",
                               visible_slots=visible_slots))

    # No separate rows for the cava visualizer — it's drawn INLINE to
    # the right of the "output_item" rows just added above (see draw()'s
    # own output_item handling), not as its own section. The one
    # exception: get_error() (a real crash/unexpected exit WHILE
    # running — deliberately NOT a missing binary, see its own
    # docstring in cava.py) gets its own row, since there's no
    # "output_item" row space to attach a warning to once rendering has
    # already skipped drawing any bars.
    if ctx.cava is not None:
        cava_error = ctx.cava.get_error()
        if cava_error:
            rows.append(("error", cava_error))

    return rows


def _draw_cava_row(stdscr, row, x, w, theme, output_row_index, num_output_rows, cava_frame):
    """One row's worth of the cava visualizer, at the box's own right
    edge — shared by the "output_item" AND "empty_slot" (output side)
    branches below, since the visualizer always spans the full
    VISIBLE_SLOTS window, not however many real sinks currently exist.
    See CLAUDE/NOTES/design-decisions.md#media-cava-and-source-label.
    """
    vis_x = x + w - 1 - CAVA_RIGHT_GAP - CAVA_VIS_WIDTH
    if cava_frame is None:
        # Idle/just-started: a flat baseline only on the bottommost row
        # of the fixed VISIBLE_SLOTS window. Plain "text" color, not
        # also dimmed (see #media-cava-and-source-label).
        if output_row_index == num_output_rows - 1:
            try:
                stdscr.addstr(row, vis_x, _CAVA_BLOCKS[1] * CAVA_VIS_WIDTH, theme.get("text", 0))
            except curses.error:
                pass
    else:
        chars = [
            _CAVA_BLOCKS[_cava_row_level(raw_height, output_row_index, num_output_rows)]
            for raw_height in cava_frame[:CAVA_VIS_WIDTH]
        ]
        try:
            stdscr.addstr(row, vis_x, "".join(chars), theme.get("text", 0))
        except curses.error:
            pass


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Media")

    inner_w = max(w - 4, 0)
    now = time.time()

    # Cava state, computed once per draw() call (not per-row): every
    # output-section row reads these same values, and num_output_rows
    # has to be known before any of them draw since _cava_row_level()
    # needs the total row count to slice each bar correctly.
    #
    # num_output_rows is always VISIBLE_SLOTS (not however many real
    # sinks exist) whenever there's at least one real sink — see
    # CLAUDE/NOTES/design-decisions.md#media-cava-and-source-label for
    # why. has_cava also requires real sink data: a visualizer for no
    # audio output at all would be nonsensical.
    sinks_for_cava = ctx.status.get("audio") if ctx.status is not None else None
    has_cava = ctx.cava is not None and bool(sinks_for_cava)
    num_output_rows = ctx.config.media_visible_slots if has_cava else 0
    cava_running = ctx.cava is not None and ctx.cava.is_running()
    cava_frame = ctx.cava.get_frame() if cava_running else None
    output_row_index = 0  # incremented once per OUTPUT-SECTION row (real or empty) drawn below

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "header":
            try:
                stdscr.addstr(row, x + 2, payload[:max(inner_w, 0)], theme.get("accent", 0) | curses.A_BOLD)
            except curses.error:
                pass

        elif kind == "error":
            try:
                stdscr.addstr(row, x + 2, f"⚠ {payload}"[:max(inner_w, 0)], theme.get("urgent", 0))
            except curses.error:
                pass

        elif kind == "empty":
            try:
                stdscr.addstr(row, x + 2, payload, theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

        elif kind == "empty_slot":
            # A specific unfilled slot within a fixed-VISIBLE_SLOTS
            # section (see _section_rows) — same dim styling as "empty"
            # above, just per-slot. An output-side empty slot still
            # gets its share of the cava visualizer, so it reserves the
            # same width real "output_item" rows do.
            is_output_slot = "output" in payload
            reserved_w = (CAVA_VIS_WIDTH + CAVA_RIGHT_GAP) if (is_output_slot and has_cava) else 0
            try:
                stdscr.addstr(row, x + 2, payload[:max(inner_w - reserved_w, 0)], theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass
            if is_output_slot and has_cava:
                _draw_cava_row(stdscr, row, x, w, theme, output_row_index, num_output_rows, cava_frame)
                output_row_index += 1

        elif kind == "player":
            player = payload
            is_this_expanded = player.bus_name == _expanded_bus_name
            # Once anything is expanded, every row's own info text (dot
            # + label) goes dimmed, including the expanded row's own —
            # see CLAUDE/NOTES/design-decisions.md#media-module-layout.
            something_expanded = _expanded_bus_name is not None
            pending = ctx.status is not None and ctx.status.is_pending("media", player.bus_name)
            dot = "●" if player.playback_status == "Playing" else "○"
            dot_color = theme.get("accent", 0) if player.playback_status == "Playing" else theme.get("text", 0)
            if something_expanded:
                dot_color |= curses.A_DIM
            if pending:
                text_color, attr = _pending_blink_style(theme)
            elif something_expanded:
                text_color, attr = theme.get("text", 0), curses.A_DIM
            else:
                is_row_selected = f"media:{player.bus_name}:row" == ctx.selected_id
                text_color = theme.get("selected", 0) if is_row_selected else theme.get("text", 0)
                attr = curses.A_BOLD if is_row_selected else 0

            # "◀◀ ▶ ▶▶" — 7 columns for the cluster: "◀◀" (2) + gap (1) +
            # play/pause (1) + gap (1) + "▶▶" (2), both internal gaps
            # exactly 1 column so the single-character play/pause glyph
            # doesn't read as uneven against the two-character arrows.
            # right_edge_gap is a separate 1-column margin so the
            # cluster doesn't render flush against the border.
            controls_w = 7
            right_edge_gap = 1
            glyph_x = x + w - 1 - right_edge_gap - controls_w
            # label_gap: same idea, 1 column between the label text and
            # the cluster — computed from glyph_x directly so it can't
            # drift out of sync with where the cluster is actually drawn.
            label_gap = 1
            label_start_x = x + 4
            available_w = max(glyph_x - label_gap - label_start_x, 0)
            body = _body_label(player)
            if body == player.identity and not player.title:
                prefix = ""
            else:
                source = _source_label(player)
                prefix = f"[{source}] " if source else ""
            prefix = prefix[:available_w]  # degrade gracefully if even the prefix alone can't fit
            body_w = max(available_w - len(prefix), 0)
            label = f"{prefix}{marquee_text(body, body_w, now)}"
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, label_start_x, label, text_color | attr)
            except curses.error:
                pass

            # Unicode from the same block as the ●/○ status dots
            # (U+25A0-25FF, "Geometric Shapes"), not "Miscellaneous
            # Technical" (⏮/⏸/⏭) — see
            # CLAUDE/NOTES/design-decisions.md#media-module-layout for
            # why. Play/pause are both exactly one character ("▶"/"■")
            # so no padding split ever needs to look centered between a
            # 1-char and 2-char glyph. "◀◀"/"▶▶" don't need this
            # treatment since they never change state.
            play_glyph = "■" if player.playback_status == "Playing" else "▶"
            prev_selected = f"media:{player.bus_name}:prev" == ctx.selected_id
            play_selected = f"media:{player.bus_name}:playpause" == ctx.selected_id
            next_selected = f"media:{player.bus_name}:next" == ctx.selected_id
            for glyph, gx, is_selected in (
                ("◀◀", glyph_x, prev_selected),
                (play_glyph, glyph_x + 3, play_selected),
                ("▶▶", glyph_x + 5, next_selected),
            ):
                if is_this_expanded and is_selected:
                    # The one truly-focused control — the only thing
                    # in this whole row allowed to actually stand out.
                    color, attr = theme.get("selected", 0), curses.A_BOLD
                else:
                    # Plain text color, not dimmed: the two non-focused
                    # controls on the expanded row should still read
                    # clearly, unlike the row's own label text above.
                    color, attr = theme.get("text", 0), 0
                try:
                    stdscr.addstr(row, gx, glyph, color | attr)
                except curses.error:
                    pass

        elif kind == "output_item":
            sink = payload
            is_selected = f"media:output:{sink.id}" == ctx.selected_id
            pending = ctx.status is not None and ctx.status.is_pending("audio", sink.id)
            if pending:
                text_color, attr = _pending_blink_style(theme)
                dot, dot_color = "●", (text_color | attr)
            else:
                dot = "●" if sink.is_default else "○"
                dot_color = theme.get("accent", 0) if sink.is_default else theme.get("text", 0)
                text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
                attr = curses.A_BOLD if is_selected else 0

            # Reserve CAVA_VIS_WIDTH (+ its right-edge gap) out of the
            # sink name's own available width whenever a visualizer will
            # actually be drawn on this row — same "compute the reserved
            # width before laying out the text" order the Now Playing
            # rows already use for their own transport-glyph cluster.
            # has_cava is computed once, above the main loop — see its
            # own docstring for why it no longer depends on THIS row.
            reserved_w = (CAVA_VIS_WIDTH + CAVA_RIGHT_GAP) if has_cava else 0
            rest = f" {sink.name}"[:max(inner_w - 1 - reserved_w, 0)]
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, rest, text_color | attr)
            except curses.error:
                pass

            if has_cava:
                _draw_cava_row(stdscr, row, x, w, theme, output_row_index, num_output_rows, cava_frame)
                output_row_index += 1


def _player_row_nav_items(player, box, row) -> list[NavItem]:
    """NavItems for one player row — either its own collapsed single row,
    or (if this is the currently-expanded player) its separate transport
    controls. Shared by nav_items()'s real (drawn) rows and its "peek"
    rows alike (see _section_nav_indices) — a peek row's `row` doesn't
    correspond to anything actually drawn THIS frame, it reuses the
    nearest real row's own y so tab_order()'s sort ties correctly
    against it (see nav_items()' own docstring for why that's safe).
    """
    x, y, w, h = box
    if player.bus_name == _expanded_bus_name:
        items = []
        controls_w = 7
        right_edge_gap = 1  # kept in sync with draw()'s own margin
        glyph_x = x + w - 1 - right_edge_gap - controls_w
        if player.can_go_previous:
            items.append(NavItem(
                id=f"media:{player.bus_name}:prev", rect=(glyph_x, row, 2, 1),
                focus_target=player.bus_name, target_kind="media_transport",
            ))
        items.append(NavItem(
            id=f"media:{player.bus_name}:playpause", rect=(glyph_x + 3, row, 1, 1),
            focus_target=player.bus_name, target_kind="media_transport",
        ))
        if player.can_go_next:
            items.append(NavItem(
                id=f"media:{player.bus_name}:next", rect=(glyph_x + 5, row, 2, 1),
                focus_target=player.bus_name, target_kind="media_transport",
            ))
        return items
    return [NavItem(
        id=f"media:{player.bus_name}:row", rect=(x + 1, row, w - 2, 1),
        focus_target=player.bus_name, target_kind="media_row",
    )]


def _output_row_nav_item(sink, box, row) -> NavItem:
    x, y, w, h = box
    return NavItem(
        id=f"media:output:{sink.id}", rect=(x + 1, row, w - 2, 1),
        focus_target=sink.id, target_kind="media_output",
    )


def nav_items(box, ctx, module_name) -> list[NavItem]:
    """Real (drawn, windowed) rows first, exactly as VISIBLE_SLOTS
    itself lays them out (see _build_rows) — then, for each section
    that's actually scrolled (more real items than fit), one extra
    "peek" NavItem just before and/or after the window, reusing the
    boundary row's own y. Tab/Shift+Tab landing on a peek item is how a
    scrollable section's hidden items become reachable at all — see
    _section_nav_indices' own docstring for why that never shows
    something undrawn as selected.
    """
    x, y, w, h = box
    players = (ctx.status.get("media") if ctx.status is not None else None) or []
    sinks = (ctx.status.get("audio") if ctx.status is not None else None) or []

    player_items: list[NavItem] = []
    player_rows: list[int] = []  # one entry per REAL "player" row drawn, in order
    output_items: list[NavItem] = []
    output_rows: list[int] = []

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "player":
            player_items.extend(_player_row_nav_items(payload, box, row))
            player_rows.append(row)
        elif kind == "output_item":
            output_items.append(_output_row_nav_item(payload, box, row))
            output_rows.append(row)

    visible_slots = ctx.config.media_visible_slots

    selected_player_index = _selected_player_index(players, ctx.selected_id, _expanded_bus_name)
    before_i, after_i = _section_nav_indices(len(players), selected_player_index, visible_slots=visible_slots)
    if before_i is not None and player_rows:
        player_items = _player_row_nav_items(players[before_i], box, player_rows[0]) + player_items
    if after_i is not None and player_rows:
        player_items = player_items + _player_row_nav_items(players[after_i], box, player_rows[-1])

    selected_output_index = _selected_output_index(sinks, ctx.selected_id)
    before_i, after_i = _section_nav_indices(len(sinks), selected_output_index, visible_slots=visible_slots)
    if before_i is not None and output_rows:
        output_items = [_output_row_nav_item(sinks[before_i], box, output_rows[0])] + output_items
    if after_i is not None and output_rows:
        output_items = output_items + [_output_row_nav_item(sinks[after_i], box, output_rows[-1])]

    return player_items + output_items


def handle_row(ctx, item, cfg):
    """Enter on a browsing-level player row expands it — mirrors
    sessions.py's handle_row exactly, including the reselect_item_id
    fix-up (see ActionContext's own docstring): nav_items() stops
    reporting this row's id the instant _expanded_bus_name changes, so
    without this the stale-selection recovery would jump to the
    sidebar instead of landing on the newly-revealed actions.
    """
    global _expanded_bus_name
    _expanded_bus_name = item.focus_target  # bus_name
    ctx.reselect_item_id = f"media:{item.focus_target}:playpause"
    return False, None


def handle_transport(ctx, item, cfg):
    action = item.id.rsplit(":", 1)[-1]  # "prev" | "playpause" | "next"
    action_name = {"prev": "previous", "playpause": "play_pause", "next": "next"}[action]
    ctx.status.request_action("media", action_name, item.focus_target)
    return False, None


def handle_output(ctx, item, cfg):
    ctx.status.request_action("audio", "set_default_sink", item.focus_target)
    return False, None


HANDLERS = {
    "media_row": handle_row,
    "media_transport": handle_transport,
    "media_output": handle_output,
}
