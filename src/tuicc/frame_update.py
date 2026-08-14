"""update_frame() — "what does this frame's world look like": WM state,
pid resolution, focus-transition detection, pending_moves processing,
two-level auto-collapse, and the RenderContext/ordered-nav-list build
that feeds draw_all() and the key-dispatch chain that follows it, both
still in main.py. NOT the key-dispatch chain itself — this never calls
a do_*()/handle_*() function or touches MODE_HANDLERS/HANDOFF_TARGETS.

Same "construction/computation, not keypress dispatch" category as
app_setup.build_app() — not unit-tested for the same reason: live I/O
throughout (provider.get_state(), status_worker.get(...), stdscr, the
D-Bus agent mailboxes) with nothing to fake it against cheaply. Does not
import config.py (receives cfg only via app.cfg), matching
resize_mode.py's own boundary.
"""

import time
from dataclasses import dataclass

from tuicc.context import RenderContext
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import tab_order, resolve_selection, module_of_item
from tuicc.render import collect_nav_items
from tuicc import procmon, pending_moves
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import media as media_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules import connectivity as connectivity_mode


def _resolve_visible_pids(windows, selected_id, resolved_pid_cache, provider, visible_slots):
    """Fills in `pid` for any procmon.WindowInfo currently missing one
    (i3 has no native pid on its IPC tree — see providers/base.py's
    resolve_pid()) via Provider.resolve_pid(), main-thread, only for
    windows within sysmon.py's currently-visible scroll window — a
    possibly-slow on-demand X11 lookup on i3, so resolving every window
    every frame would waste work on a long, scrolled-out-of-view list.
    Resolved pids are cached indefinitely in `resolved_pid_cache` (not
    on WindowInfo, rebuilt fresh every frame) — a closed window's
    orphaned entry is harmless, same accepted-growth tradeoff as
    CLAUDE/NOTES/wm-quirks.md#no-focus-pid-criteria.
    """
    visible_ids = sysmon_mode.visible_window_ids(windows, selected_id, visible_slots)
    resolved = []
    for w in windows:
        pid = w.pid
        if pid is None:
            pid = resolved_pid_cache.get(w.window_id)
        if pid is None and w.window_id in visible_ids:
            pid = provider.resolve_pid(w.window_id)
            if pid is not None:
                resolved_pid_cache[w.window_id] = pid
        resolved.append(procmon.WindowInfo(window_id=w.window_id, app_id=w.app_id, title=w.title, pid=pid))
    return resolved


@dataclass
class FrameResult:
    """What update_frame() computed this frame that the dispatch chain
    (still in main()) needs again. boxes/term_width/term_height/ordered/
    selected_item are per-frame-only, deliberately excluded from
    LoopState (same reasoning loop_state.py's own docstring gives) —
    this-frame-computed context, not cross-frame session state. state
    is NOT a separate field — ctx.state already holds it
    (ctx = RenderContext(state=state, ...)); returning it twice would
    just be a desync risk with nothing enforcing the two stay equal.
    """
    ctx: RenderContext
    boxes: dict
    term_width: int
    term_height: int
    ordered: list
    selected_item: object | None


def update_frame(stdscr, app, loop_state, resize, spawn_picker, help_state, launcher, moves) -> FrameResult:
    cfg = app.cfg
    control_colors = app.control_colors
    provider = app.provider
    wifi_agent = app.wifi_agent
    bluez_agent = app.bluez_agent
    pid_feed = app.pid_feed
    status_worker = app.status_worker
    cava_reader = app.cava_reader
    action_ctx = app.action_ctx

    # Drives a faster redraw cadence below — the idle 1000ms cadence
    # alone would turn the marquee's smooth 1-char slide into a visible
    # jump.
    marquee_active = media_mode.has_scrolling_content(status_worker.get("media"))

    # Lazy: only run the visualizer while something could show AND the
    # current preset even has a media box for it.
    media_module_present = any(b.name == "media" for b in cfg.layout.boxes)
    media_players = status_worker.get("media") or []
    any_playing = any(p.playback_status == "Playing" for p in media_players)
    if media_module_present and any_playing:
        cava_reader.start()
    else:
        cava_reader.stop()

    # Runs every frame, before getch() reads a key — a live iwd/bluez
    # daemon callback can cancel a passphrase/pairing prompt at any
    # moment, so this claims mode_stack ahead of any keypress-driven
    # claim, not by dispatch order (moot now — one stack, one top). See
    # CLAUDE/NOTES/design-decisions.md#mode-stack-phase-1.
    #
    # Must not close on the Enter keypress itself — iwd/bluez haven't
    # tried the passphrase/pairing yet at that point, only
    # mark_*_submitted() has run (modules/connectivity.py).
    if connectivity_mode.is_entering_passphrase() and connectivity_mode.is_passphrase_waiting():
        ssid = connectivity_mode.entering_passphrase_ssid()
        if not status_worker.is_pending("wifi", ssid):
            error = status_worker.get_action_error_for("wifi", ssid)
            if error:
                connectivity_mode.set_passphrase_error(error)
            else:
                connectivity_mode.cancel_passphrase_entry()
                loop_state.mode_stack.pop()
    if connectivity_mode.is_confirming_pairing() and connectivity_mode.is_pairing_waiting():
        pairing_request = connectivity_mode.current_pairing_request()
        if not status_worker.is_pending("bluetooth", pairing_request.device_id):
            error = status_worker.get_action_error_for("bluetooth", pairing_request.device_id)
            if error:
                connectivity_mode.set_pairing_error(error)
            else:
                connectivity_mode.cancel_pairing_confirm()
                loop_state.mode_stack.pop()

    connectivity_wants_input = (
        loop_state.pending_confirm is None
        and not resize.active and not spawn_picker.active and not help_state.active
        and loop_state.mode_stack[-1] == "normal"
    )
    # Also fires mid-retry (iwd re-asking RequestPassphrase after a
    # wrong password) — gated to only when actually WAITING on a
    # result, not while still typing, or has_pending() alone would
    # re-fire every frame and wipe out what's being typed.
    if wifi_agent.mailbox.has_pending() and (
        connectivity_wants_input
        or (loop_state.mode_stack[-1] == "connectivity_passphrase" and connectivity_mode.is_passphrase_waiting())
    ):
        request = wifi_agent.mailbox.get_request()
        connectivity_mode.start_passphrase_entry(request.ssid)
        # Guarded: the retry case above reaches here with the frame
        # already pushed — a bare append would duplicate it, and the
        # eventual single pop() wouldn't fully undo that.
        if loop_state.mode_stack[-1] != "connectivity_passphrase":
            loop_state.mode_stack.append("connectivity_passphrase")
    elif bluez_agent is not None and bluez_agent.mailbox.has_pending() and (
        connectivity_wants_input
        or (loop_state.mode_stack[-1] == "connectivity_pairing" and connectivity_mode.is_pairing_waiting())
    ):
        request = bluez_agent.mailbox.get_request()
        connectivity_mode.start_pairing_confirm(request)
        if loop_state.mode_stack[-1] != "connectivity_pairing":  # see passphrase branch above
            loop_state.mode_stack.append("connectivity_pairing")

    agent_has_pending = (
        wifi_agent.mailbox.has_pending()
        or (bluez_agent is not None and bluez_agent.mailbox.has_pending())
    )
    stdscr.timeout(
        50 if (moves.entries or action_ctx.restore_queue or status_worker.has_pending()
               or loop_state.resize_message is not None or agent_has_pending)
        else int(media_mode.CAVA_REDRAW_SECONDS * 1000) if cava_reader.is_running()
        else int(media_mode.MARQUEE_STEP_SECONDS * 1000) if marquee_active
        # 300, not 1000: draw() reruns unconditionally every frame
        # regardless of cadence, so redrawing more often while idle is
        # free — and a 1s idle cadence would stack atop Domains already
        # polling as fast as 1s.
        else 300
    )
    stdscr.erase()

    term_height, term_width = stdscr.getmaxyx()
    boxes = compute_boxes(cfg.layout, term_width, term_height)
    state = provider.get_state()

    # Publishes this frame's window list for the background "windows"
    # Domain (procmon.py) — must run before this frame's own
    # RenderContext/nav_items below, and sorted the same way
    # sysmon.py's own display sort is, or the lazy pid-resolution below
    # picks a different "visible" set than what's about to be shown.
    windows_this_frame = sysmon_mode.sort_windows_by_drain(
        procmon.flatten_windows(state), known_stats=status_worker.get("windows") or [],
    )
    pid_feed.set(_resolve_visible_pids(
        windows_this_frame, loop_state.selected_id, loop_state.resolved_pid_cache, provider, cfg.sysmon_visible_slots,
    ))

    if state.focused_region_id is not None and state.focused_region_id != loop_state.last_focused_region_id:
        loop_state.origin_region_id = loop_state.last_focused_region_id
        loop_state.last_focused_region_id = state.focused_region_id
        if loop_state.expect_focus_reclaim:
            # Self-inflicted (tuicc reclaiming its own focus after a
            # spawn/restore) — skip the reset below, but still update
            # origin/last_focused_region_id above.
            loop_state.expect_focus_reclaim = False
        else:
            # A real WM-focus transition (dismissed, workspace
            # switched, resummoned elsewhere) — force a re-sync via the
            # still_valid recovery block below, or focus_id/selected_id
            # stay pinned to a stale context and every launcher spawn
            # keeps targeting it.
            loop_state.selected_id = None

    if action_ctx.restore_queue:
        known_ids = {w.id for r in state.regions for w in r.windows}
        pending_moves.promote_restore_queue(moves, provider, action_ctx.restore_queue, known_ids, time.monotonic())

    if moves.entries:
        current_windows = [w for r in state.regions for w in r.windows]
        result = pending_moves.process(
            moves, provider, current_windows, loop_state.dismissed, time.monotonic(), cfg.fullscreen_only,
            own_region_id=loop_state.last_focused_region_id,
        )
        if result.reclaimed_focus:
            loop_state.expect_focus_reclaim = True
        if result.resolved_target_regions and (loop_state.focus_id is None or loop_state.focus_id == loop_state.last_focused_region_id):
            # expect_focus_reclaim suppresses the real-transition reset
            # while a restore/spawn is resolving, so focus_id
            # (preview.py's own target) never otherwise gets to follow
            # where it landed. Only auto-follow when nothing was
            # deliberately selected elsewhere — LOAD-BEARING: without
            # this the preview stayed permanently blank after a session
            # restore.
            loop_state.focus_id = result.resolved_target_regions[-1]
        if result.failure_messages:
            # Single-line toast, one at a time — a rare same-frame
            # multi-failure round silently keeps only the last, matching
            # this mechanism's existing one-thing-at-a-time nature (the
            # resize/spawn-picker hints already only ever show one
            # thing too). Longer than the routine 3.0s save/preset
            # toasts (loop_state.py's own doc comment) since a failure
            # is more important to actually notice.
            loop_state.resize_message = result.failure_messages[-1]
            loop_state.resize_message_until = time.monotonic() + 5.0
            loop_state.resize_message_urgent = True

    # A two-level module's expanded state may only be left via Escape
    # or picking an action, never silently by navigating elsewhere —
    # one check per frame catches every way active_module can change,
    # instead of patching each site.
    if loop_state.active_module != "sessions" and sessions_mode.is_expanded():
        sessions_mode.collapse()
    if loop_state.active_module != "media" and media_mode.is_expanded():
        media_mode.collapse()
    if loop_state.active_module != "sysmon" and sysmon_mode.is_expanded():
        sysmon_mode.collapse()

    ctx = RenderContext(
        state=state,
        selected_id=loop_state.selected_id,
        focus_id=loop_state.focus_id,
        theme=loop_state.theme_pairs,
        config=cfg,
        pending_confirm=loop_state.pending_confirm,
        active_module=loop_state.active_module,
        typing_mode=launcher.typing_mode,
        search_query=launcher.search_query,
        search_selected_index=launcher.search_selected_index,
        wifi_networks=status_worker.get("wifi"),
        bluetooth_devices=status_worker.get("bluetooth"),
        # Poll failure (backend unreachable) takes priority over an
        # agent registration failure — the more complete outage.
        wifi_error=status_worker.get_error("wifi") or wifi_agent.get_error(),
        bluetooth_error=status_worker.get_error("bluetooth") or (bluez_agent.get_error() if bluez_agent else None),
        status=status_worker,
        session_preview=sessions_mode.expanded_preview(),
        control_colors=control_colors,
        cava=cava_reader,
    )

    items = collect_nav_items(cfg.layout, boxes, ctx)
    ordered = tab_order(items, mode=cfg.tab_order)

    still_valid = any(item.id == loop_state.selected_id for item in ordered)
    # LOAD-BEARING: a Left/Right jump onto an empty module sets
    # selected_id=None on purpose. Without this check, still_valid is
    # False for that same reason and the recovery below immediately
    # undoes the jump on the very same frame.
    intentionally_unselected = loop_state.selected_id is None and not any(
        module_of_item(item) == loop_state.active_module for item in ordered
    )
    if not still_valid and not intentionally_unselected:
        match = None
        for item in ordered:
            if item.target_kind == "region" and item.focus_target == state.focused_region_id:
                match = item
                break
        if match is None and ordered:
            match = ordered[0]
        if match is not None:
            # Through resolve_selection(), not a hand-rolled
            # selected_id-only update — otherwise focus_id/active_module
            # can drift out of sync with it (found live: preview kept
            # showing a stale workspace).
            loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(match, loop_state.focus_id)
        else:
            loop_state.selected_id = None
        ctx.selected_id = loop_state.selected_id
        ctx.focus_id = loop_state.focus_id
        ctx.active_module = loop_state.active_module

    selected_item = None
    for item in ordered:
        if item.id == loop_state.selected_id:
            selected_item = item
            break
    ctx.selected_item = selected_item

    return FrameResult(
        ctx=ctx, boxes=boxes, term_width=term_width, term_height=term_height,
        ordered=ordered, selected_item=selected_item,
    )
