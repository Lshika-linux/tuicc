"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import sys
import time
import locale
from pathlib import Path

locale.setlocale(locale.LC_ALL, "")

# Relative to main.py's own location, not to cwd — so tuicc works whether
# you launch it via `cd tuicc && python main.py` (cwd == tuicc) or via a
# WM keybind spawning it from an arbitrary directory (e.g. a floating
# terminal launched with a custom app_id, cwd defaults to $HOME).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tuicc.config import (
    load_config,
    save_layout_to_preset,
    available_preset_numbers,
    set_active_preset,
    set_theme_color,
    set_session_name,
    get_raw_theme_values,
    get_raw_navigation_keys,
    get_raw_power_menu_actions,
    build_layout_from_preset,
)
from tuicc.context import RenderContext
from tuicc.actions import ActionContext, spawn_detached, handle_pending_confirm, dispatch_action
from tuicc.providers.registry import build_provider
from tuicc.connectivity.registry import build_wifi_backend, build_bluetooth_backend
from tuicc.connectivity.worker import ConnectivityWorker
from tuicc.layout import ModuleBox
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import (
    tab_order,
    resolve_selection,
    global_shortcut_item,
    next_module_name,
    prev_module_name,
    first_item_in_module,
    next_item_across_modules,
    prev_item_across_modules,
    same_row_neighbor,
    module_of_item,
)
from tuicc.render import draw_all, collect_nav_items, ACTION_HANDLERS, MODULES
from tuicc.render_utils import draw_status_line
from tuicc.theme_setup import setup_theme, reassign_theme_pairs
from tuicc import resize_mode, help_mode, pending_moves
from tuicc.modules import launcher as launcher_mode
from tuicc.modules import sessions as sessions_mode


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    provider = build_provider(cfg.provider_name)
    provider.mark_self(cfg.self_app_id)

    wifi_backend = build_wifi_backend(cfg.wifi_backend_name)
    bluetooth_backend = build_bluetooth_backend(cfg.bluetooth_backend_name)
    connectivity = ConnectivityWorker(wifi_backend, bluetooth_backend)
    connectivity.start()

    action_ctx = ActionContext(provider=provider, connectivity=connectivity)

    # Only used by resize mode's box-editing tier for resize/move math —
    # normal navigation (below) no longer does spatial movement, so it
    # doesn't consume this dict at all anymore.
    direction_keys = {
        cfg.keybinds["left"]: "left",
        cfg.keybinds["right"]: "right",
        cfg.keybinds["up"]: "up",
        cfg.keybinds["down"]: "down",
    }
    if cfg.vim_mode:
        direction_keys[cfg.keybinds["vim_left"]] = "left"
        direction_keys[cfg.keybinds["vim_right"]] = "right"
        direction_keys[cfg.keybinds["vim_up"]] = "up"
        direction_keys[cfg.keybinds["vim_down"]] = "down"

    # Navigation key-sets: next/prev item (Tab/Shift+Tab + Down/Up as
    # duplicates, rolling into the next/previous module at either end
    # — see navigation.py's next_item_in_module/prev_item_in_module)
    # vs. an explicit jump straight to the next/previous module's first
    # item (Left/Right). vim hjkl duplicate all four, but only when
    # vim_mode is on — see the [navigation.keys] comment on why.
    next_item_keys = {cfg.keybinds["tab"], cfg.keybinds["down"]}
    prev_item_keys = {cfg.keybinds["previous"], cfg.keybinds["up"]}
    module_next_keys = {cfg.keybinds["right"]}
    module_prev_keys = {cfg.keybinds["left"]}
    if cfg.vim_mode:
        next_item_keys.add(cfg.keybinds["vim_down"])
        prev_item_keys.add(cfg.keybinds["vim_up"])
        module_next_keys.add(cfg.keybinds["vim_right"])
        module_prev_keys.add(cfg.keybinds["vim_left"])

    selected_id = None
    focus_id = None
    pending_confirm = None
    active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None

    # True from the moment dismiss_self() is called until the next real
    # keypress arrives (a keypress can only reach tuicc's own terminal
    # while it's focused, the same assumption ambient typing already
    # relies on) — lets pending_moves.process() below know not to call
    # focus_self() while tuicc is deliberately hidden. Without this, a
    # window that finishes spawning after tuicc was dismissed would
    # have focus_self() pull tuicc back onto the screen on its own,
    # since focusing a scratchpadded window un-hides it on sway/i3.
    #
    # KNOWN LIMITATION: resets on the next KEYPRESS, not the next
    # resummon — so a pending_moves entry that resolves after you've
    # resummoned tuicc but before you've pressed anything yet still
    # skips focus_self(), even though tuicc is genuinely visible again
    # at that point. Narrow (needs the spawned window to finish moving
    # in that exact gap) and not worth tightening further right now —
    # same category of accepted race as mark_self()'s focus-based
    # fallback (see providers/base.py).
    dismissed = False

    # Tracks the region focused right before tuicc's own — used by
    # return_to_origin's top-level Escape dismiss. Can't just read
    # state.focused_region_id at dismiss time: parse_tree() doesn't
    # filter tuicc's own marked window out of the *focused* lookup
    # (only out of each region's windows list), so whenever tuicc
    # itself has WM focus, focused_region_id already IS tuicc's own
    # region. Tracking the value being *replaced* on every real
    # transition instead means origin_region_id stays correctly frozen
    # at "wherever you were before you opened tuicc" for the whole
    # time you're only navigating inside tuicc (arrows/Tab never call
    # focus_region/focus_window, so no further transition fires).
    last_focused_region_id = None
    origin_region_id = None
    # True for exactly one frame after pending_moves.process() calls
    # provider.focus_self() — a real WM-focus transition, but a
    # self-inflicted one (tuicc reclaiming its own focus after a
    # spawn/restore resolves), not the user having switched to a
    # different real context. The transition detector below reads and
    # clears this to tell the two apart — without it, a focus_self()
    # landing between a sidebar selection and confirming a launcher
    # spawn silently resets that selection, and the spawn targets
    # wherever real focus happened to land instead of what was picked.
    expect_focus_reclaim = False

    # Session state for the input-hijacking/queueing concerns this file
    # coordinates but doesn't itself contain the logic for — see
    # resize_mode.py/help_mode.py/launcher.py's LauncherState/
    # pending_moves.py's PendingMovesQueue. This file owns *when* to
    # call their functions (which key means what, in what order), not
    # the state transitions or math themselves.
    resize = resize_mode.ResizeState()
    spawn_picker = resize_mode.SpawnPickerState()
    help_state = help_mode.HelpState()
    launcher = launcher_mode.LauncherState()
    moves = pending_moves.PendingMovesQueue()

    # A generic transient toast — used by save/cycle-preset as much as
    # by resize, genuinely main-loop-level, not owned by either module.
    resize_message = None
    resize_message_until = 0.0

    def do_spawn_picker():
        available = set(MODULES.keys()) - {b.name for b in cfg.layout.boxes}
        resize_mode.open_picker(spawn_picker, available)

    def do_enter_resize():
        resize_mode.enter_edit_mode(resize)

    def do_save_layout():
        nonlocal resize_message, resize_message_until
        save_layout_to_preset(cfg.layout, cfg.preset_number)
        resize_message = f"Saved preset {cfg.preset_number}"
        resize_message_until = time.monotonic() + 3.0
        resize_mode.exit_edit_mode(resize)

    def do_cycle_preset():
        nonlocal active_module, resize_message, resize_message_until
        numbers = available_preset_numbers()
        if numbers:
            idx = numbers.index(cfg.preset_number) if cfg.preset_number in numbers else -1
            next_number = numbers[(idx + 1) % len(numbers)]
            cfg.layout = build_layout_from_preset(next_number)
            set_active_preset(next_number)
            cfg.preset_number = next_number
            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
            resize_message = f"preset {next_number}"
            resize_message_until = time.monotonic() + 3.0
        resize_mode.exit_edit_mode(resize)

    def do_enter_help():
        help_mode.enter(help_state)

    def do_apply_reselect():
        # See ActionContext.reselect_region_id/reselect_item_id's
        # docstrings — both consumed once, right after any dispatch/
        # confirm site that might have set either.
        nonlocal selected_id, active_module, focus_id
        if action_ctx.reselect_item_id is not None:
            # Direct set, no lookup against `ordered` — deliberately:
            # `ordered` here still reflects nav_items() from BEFORE the
            # handler that set this ran (it's computed once at the top
            # of the frame, dispatch happens after), so the id this
            # names typically isn't in it yet. module_of_item's own
            # "modulename:id" convention is all that's needed to derive
            # active_module from a plain string, no real NavItem
            # required — next frame's nav_items() recomputes for real
            # and the normal "is selected_id still valid" check finds
            # it there, same as any other selection.
            selected_id = action_ctx.reselect_item_id
            active_module = selected_id.split(":")[0]
            action_ctx.reselect_item_id = None
            return
        # Looks up a real region NavItem instead of hardcoding an
        # id-prefix convention here, since which module actually owns
        # "region" items (sidebar vs sidebar_compact) is a preset/
        # config choice, not something main.py should assume.
        if action_ctx.reselect_region_id is None:
            return
        region_item = next(
            (it for it in ordered if it.target_kind == "region" and it.focus_target == action_ctx.reselect_region_id),
            None,
        )
        if region_item is not None:
            selected_id, active_module, focus_id = resolve_selection(region_item, focus_id)
        action_ctx.reselect_region_id = None

    # No `break` anywhere below this point — tuicc's lifecycle model
    # (VISION.md section 2) is a persistent process the WM shows/hides;
    # every former "exit" site now calls provider.dismiss_self() and
    # keeps looping. The only way out is an unhandled exception, in
    # practice Ctrl+C (caught at the bottom of this file) — this
    # try/finally is what makes that a clean shutdown rather than a
    # daemon thread just getting killed mid-poll.
    try:
        while True:
            stdscr.timeout(
                50 if (moves.entries or action_ctx.restore_queue or connectivity.has_pending()
                       or resize_message is not None) else 1000
            )
            stdscr.erase()

            term_height, term_width = stdscr.getmaxyx()
            boxes = compute_boxes(cfg.layout, term_width, term_height)
            state = provider.get_state()

            if state.focused_region_id is not None and state.focused_region_id != last_focused_region_id:
                origin_region_id = last_focused_region_id
                last_focused_region_id = state.focused_region_id
                if expect_focus_reclaim:
                    # tuicc reclaiming its own focus after a spawn/
                    # restore resolved (pending_moves.process()'s
                    # focus_self() call last frame) — a real transition
                    # by the check above, but self-inflicted, not the
                    # user having gone anywhere. Skip the reset below;
                    # still update origin/last_focused_region_id above,
                    # since those track real focus regardless of cause.
                    expect_focus_reclaim = False
                else:
                    # A real WM-focus transition — as opposed to just
                    # browsing tuicc's own sidebar, which deliberately never
                    # touches real focus (that's what lets you target a
                    # spawn at a workspace without actually switching to it
                    # first) — most commonly means tuicc was just dismissed
                    # to the scratchpad, you switched real workspaces, and
                    # resummoned it somewhere new. Force a re-sync: without
                    # this, focus_id/selected_id stay pinned to wherever you
                    # last left the cursor, possibly in a completely
                    # different real context, and every launcher spawn
                    # silently keeps targeting that stale workspace instead
                    # of wherever you actually are now. Invalidating
                    # selected_id here (rather than resetting focus_id
                    # directly) reuses the still_valid recovery block right
                    # below, which already correctly re-derives selected_id/
                    # active_module/focus_id together via resolve_selection.
                    selected_id = None

            if action_ctx.restore_queue:
                known_ids = {w.id for r in state.regions for w in r.windows}
                pending_moves.promote_restore_queue(moves, provider, action_ctx.restore_queue, known_ids, time.monotonic())

            if moves.entries:
                current_windows = [w for r in state.regions for w in r.windows]
                # See Provider.focus_self()'s docstring for why reclaiming
                # focus on a match isn't optional-feeling, and pending_moves.
                # process()'s own docstring for why it's skipped while
                # dismissed (and for what its return value means, consumed
                # by the transition detector above on the NEXT frame).
                reclaimed_focus, resolved_target_regions = pending_moves.process(
                    moves, provider, current_windows, dismissed, time.monotonic(), cfg.fullscreen_only,
                    own_region_id=last_focused_region_id,
                )
                if reclaimed_focus:
                    expect_focus_reclaim = True
                if resolved_target_regions and (focus_id is None or focus_id == last_focused_region_id):
                    # expect_focus_reclaim (above) suppresses the real-
                    # focus-transition reset for as long as a restore/
                    # spawn is still resolving matches — necessarily, since
                    # tuicc reclaiming its own focus each round would
                    # otherwise look identical to a real external
                    # transition and wipe out the in-progress session's
                    # selection. But that also means focus_id (and so the
                    # preview panel, which follows focus_id — see
                    # preview.py) never gets a chance to move to wherever
                    # the restore/spawn actually landed, until something
                    # else eventually forces a real reset (dismiss+
                    # resummon). Only auto-follow when focus_id currently
                    # just mirrors tuicc's own live region (nothing
                    # deliberately selected elsewhere in the sidebar) — a
                    # manually-selected focus_id must never be silently
                    # overridden by a spawn resolving in the background.
                    # Found live: preview staying blank forever (not just
                    # during the transient co-location window) after a
                    # session restore completed, for the rest of that
                    # same tuicc toggle.
                    focus_id = resolved_target_regions[-1]

            # A session slot's level-2 (expanded) state is meant to be
            # left only by Escape or picking an action — never silently
            # by navigating elsewhere while it's still open. Checked
            # here, once per frame, rather than patched into every
            # individual place active_module can change (Tab/Shift+Tab
            # rolling out, Left/Right, ambient typing into the
            # launcher, F1/F2/F6, ...) — active_module already reflects
            # whatever the previous frame's keypress did by the time
            # this runs, so one check here reliably catches all of
            # them. No selected_id/focus_id fixup needed alongside it:
            # whatever changed active_module away from "sessions" also
            # already moved selected_id off of any sessions:action:*
            # id via its own resolve_selection call.
            if active_module != "sessions" and sessions_mode.is_expanded():
                sessions_mode.collapse()

            ctx = RenderContext(
                state=state,
                selected_id=selected_id,
                focus_id=focus_id,
                theme=theme_pairs,
                config=cfg,
                pending_confirm=pending_confirm,
                active_module=active_module,
                typing_mode=launcher.typing_mode,
                search_query=launcher.search_query,
                search_selected_index=launcher.search_selected_index,
                wifi_networks=connectivity.get_wifi_networks(),
                bluetooth_devices=connectivity.get_bluetooth_devices(),
                connectivity=connectivity,
            )

            items = collect_nav_items(cfg.layout, boxes, ctx)
            ordered = tab_order(items, mode=cfg.tab_order)

            still_valid = any(item.id == selected_id for item in ordered)
            if not still_valid:
                match = None
                for item in ordered:
                    if item.target_kind == "region" and item.focus_target == state.focused_region_id:
                        match = item
                        break
                if match is None and ordered:
                    match = ordered[0]
                if match is not None:
                    # Goes through the same resolve_selection() real
                    # keyboard navigation uses, not a hand-rolled partial
                    # update — this recovery path used to only touch
                    # selected_id, leaving focus_id (and active_module)
                    # stuck at whatever they were before. That let
                    # sidebar's highlight (driven by this block) and
                    # preview's target (driven by focus_id) silently
                    # drift apart — e.g. right after typing_mode exits
                    # and selected_id gets auto-recovered to wherever's
                    # live-focused, focus_id stayed pinned to an earlier
                    # explicit selection, so preview kept showing that
                    # stale workspace while sidebar looked perfectly
                    # live. Routing through resolve_selection keeps all
                    # three in lockstep regardless of how selected_id
                    # ends up changing.
                    selected_id, active_module, focus_id = resolve_selection(match, focus_id)
                else:
                    selected_id = None
                ctx.selected_id = selected_id
                ctx.focus_id = focus_id
                ctx.active_module = active_module

            selected_item = None
            for item in ordered:
                if item.id == selected_id:
                    selected_item = item
                    break
            ctx.selected_item = selected_item

            if help_state.active:
                help_mode.draw(
                    stdscr, term_width, term_height, theme_pairs, help_state,
                    get_raw_navigation_keys(), get_raw_power_menu_actions(), get_raw_theme_values(),
                )
            else:
                draw_all(stdscr, cfg.layout, boxes, ctx)

                if resize.editing and active_module in boxes:
                    resize_mode.draw_editing_highlight(stdscr, boxes[active_module], theme_pairs)

                if spawn_picker.active:
                    draw_status_line(stdscr, term_width, resize_mode.spawn_hint_text(spawn_picker), theme_pairs.get("urgent", 0))
                elif resize.active:
                    draw_status_line(stdscr, term_width, resize_mode.hint_text(resize, active_module), theme_pairs.get("urgent", 0))
                elif resize_message is not None:
                    if time.monotonic() < resize_message_until:
                        draw_status_line(stdscr, term_width, resize_message, theme_pairs.get("accent", 0))
                    else:
                        resize_message = None

            stdscr.refresh()

            key = stdscr.getch()

            if key == -1:
                continue
            dismissed = False

            if pending_confirm is not None:
                should_dismiss, pending_confirm = handle_pending_confirm(action_ctx, pending_confirm, key, cfg)
                do_apply_reselect()
                if should_dismiss:
                    dismissed = True
                    provider.dismiss_self()
                continue

            global_item = global_shortcut_item(cfg.global_shortcuts, key)
            if global_item is not None:
                # pending_confirm is always None here — the tier above
                # already intercepted and continued otherwise (see
                # dispatch_action's docstring for why this makes an
                # unconditional assignment safe).
                should_dismiss, pending_confirm = dispatch_action(action_ctx, ACTION_HANDLERS, global_item, cfg)
                do_apply_reselect()
                if should_dismiss:
                    dismissed = True
                    provider.dismiss_self()
                continue

            if sessions_mode.is_naming():
                # Same shape as help_state's color editor just below —
                # a narrow key-capture hijack, not a full modal like
                # help_state.active/resize.active. The early `continue`
                # here is what keeps Tab/other keys from leaking through
                # to the normal dispatch chain mid-rename (handle_naming_key
                # itself just ignores anything outside backspace/Escape/
                # printable-ASCII, so a stray Tab is silently a no-op,
                # not a navigation change).
                if key == cfg.keybinds["confirm"]:
                    result = sessions_mode.apply_naming()
                    if result is not None:
                        slot, new_name = result
                        cfg.session_names[slot] = new_name or f"Slot {slot}"
                        set_session_name(slot, new_name)
                else:
                    sessions_mode.handle_naming_key(key)
                continue

            if help_state.active:
                if help_state.page is None:
                    help_mode.select_page(help_state, key)
                    if help_state.page is None and key == 27:  # Escape
                        help_state.active = False
                    continue

                if help_state.page == "colors":
                    if help_state.color_editing:
                        if key == cfg.keybinds["confirm"]:
                            result = help_mode.apply_color_edit(help_state)
                            if result is not None:
                                role, color, typed_value = result
                                cfg.theme[role] = color
                                theme_pairs = reassign_theme_pairs(cfg.theme)
                                set_theme_color(role, typed_value)
                        else:
                            help_mode.type_color_key(help_state, key)
                        continue
                    if key == cfg.keybinds["up"]:
                        help_mode.move_color_index(help_state, -1)
                    elif key == cfg.keybinds["down"]:
                        help_mode.move_color_index(help_state, 1)
                    elif key == cfg.keybinds["confirm"]:
                        help_mode.start_color_edit(help_state, get_raw_theme_values())
                    elif key == 27:  # Escape
                        help_state.page = None
                    continue

                if key == 27:  # Escape
                    help_state.page = None
                continue

            if launcher.typing_mode:
                if key == cfg.keybinds["confirm"]:
                    selected = launcher_mode.resolve_selected(launcher)
                    if selected is not None:
                        cmd, app_id_hint = selected
                        known_ids = {w.id for r in state.regions for w in r.windows}
                        # .desktop Exec= is spec'd to never be shell-interpreted.
                        pid = spawn_detached(cmd, shell_true=False)
                        # See Provider.no_focus_next_window()'s docstring —
                        # asked for right after the pid is known, well
                        # before the spawned window has had a chance to
                        # map and steal focus/fullscreen from tuicc.
                        provider.no_focus_next_window(pid)
                        # focus_id is only ever set by explicitly selecting a
                        # sidebar region item — without one selected (or
                        # without a sidebar in the layout at all), fall back
                        # to whatever's actually focused right now, same
                        # live-follow pattern preview.py's draw() uses.
                        # app_id_hint (see launcher.scan_desktop_apps) is only
                        # a fallback — resolve_pending_move always tries the
                        # pid tier first, this just gives process()'s
                        # grace-period downgrade something to fall back to
                        # for single-instance apps whose pid will never
                        # appear on a window.
                        pending_moves.queue_launcher_spawn(
                            moves,
                            focus_id if focus_id is not None else state.focused_region_id,
                            known_ids, pid, app_id_hint, time.monotonic(),
                        )
                        launcher_mode.exit_typing_mode(launcher)
                        selected_id = launcher.saved_selected_id
                        active_module = launcher.saved_active_module
                    # selected is None (no search results): nothing happens,
                    # typing_mode stays True — not an implicit cancel.
                else:
                    launcher_mode.handle_typing_key(launcher, key, cfg)
                    if not launcher.typing_mode:
                        selected_id = launcher.saved_selected_id
                        active_module = launcher.saved_active_module
                continue

            if spawn_picker.active:
                choice = resize_mode.choose(spawn_picker, key)
                if choice is not None:
                    new_box = ModuleBox(name=choice, x=0.4, y=0.4, w=0.2, h=0.2)
                    cfg.layout.boxes.append(new_box)
                    active_module = choice
                    resize_mode.enter_box_editing(resize, new_box, is_new=True)
                continue

            # Browsing level: the edit session is open but no module is being
            # resized/moved right now — everything except confirm/delete_box/
            # Escape falls through to the normal dispatch chain below, so
            # Tab/Shift+Tab/arrow navigation and F1/F3/F4/F6 all keep
            # working exactly as outside the session.
            if resize.active and not resize.editing:
                if resize.confirm_delete:
                    if key == cfg.keybinds["confirm_yes"]:
                        deleted_name = resize.box.name
                        resize_mode.confirm_delete_yes(resize, cfg.layout.boxes)
                        if active_module == deleted_name:
                            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
                    elif key == cfg.keybinds["confirm_no"]:
                        resize_mode.confirm_delete_no(resize)
                    continue
                if key == cfg.keybinds["confirm"] and active_module is not None:
                    box = next((b for b in cfg.layout.boxes if b.name == active_module), None)
                    if box is not None:
                        resize_mode.enter_box_editing(resize, box)
                    continue
                elif key == cfg.keybinds["delete_box"] and active_module is not None:
                    box = next((b for b in cfg.layout.boxes if b.name == active_module), None)
                    if box is not None:
                        resize_mode.request_delete(resize, box)
                    continue
                elif key == 27:  # Escape
                    resize_mode.exit_edit_mode(resize)
                    continue
                # else: fall through to the bottom dispatch chain.

            elif resize.active and resize.editing:
                if resize.confirm_delete:
                    if key == cfg.keybinds["confirm_yes"]:
                        deleted_name = resize.box.name
                        resize_mode.confirm_delete_yes(resize, cfg.layout.boxes)
                        if active_module == deleted_name:
                            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
                    elif key == cfg.keybinds["confirm_no"]:
                        resize_mode.confirm_delete_no(resize)
                    continue
                if key in direction_keys:
                    x_cells, y_cells, w_cells, h_cells = boxes[active_module]
                    resize_mode.apply_direction(
                        resize, direction_keys[key], term_width, term_height, x_cells, y_cells, w_cells, h_cells
                    )
                elif key == cfg.keybinds["move_toggle"]:
                    resize_mode.toggle_dimension(resize)
                elif key == cfg.keybinds["delete_box"]:
                    resize_mode.request_delete(resize, resize.box)
                elif key == cfg.keybinds["confirm"]:
                    # Keeps the change in cfg.layout (in memory only) and
                    # returns to browsing — resize as many other modules as
                    # you like before writing anything to disk via save_layout.
                    resize_mode.commit_box_editing(resize)
                elif key == 27:  # Escape
                    resize_mode.escape_box_editing(resize, cfg.layout.boxes)
                elif key == cfg.keybinds["spawn_box"]:
                    resize_mode.commit_box_editing(resize)
                    do_spawn_picker()
                elif key == cfg.keybinds["resize"]:
                    resize_mode.commit_box_editing(resize)
                    do_enter_resize()
                elif key == cfg.keybinds["save_layout"]:
                    resize_mode.commit_box_editing(resize)
                    do_save_layout()
                elif key == cfg.keybinds["cycle_preset"]:
                    resize_mode.commit_box_editing(resize)
                    do_cycle_preset()
                elif key == cfg.keybinds["help"]:
                    resize_mode.commit_box_editing(resize)
                    do_enter_help()
                continue

            # Sorted by position, not declaration order in the preset
            # file — module-to-module movement (Left/Right, and Tab/
            # Shift+Tab rolling past a module's last/first item) should
            # feel spatially sensible even though it's not spatial
            # search anymore. Same sort key tab_order() already uses
            # for items within a module, just applied to each module's
            # own box instead of a NavItem's rect.
            module_position_key = (
                (lambda box: (box.y, box.x)) if cfg.tab_order == "rows_first"
                else (lambda box: (box.x, box.y))
            )
            module_names = [box.name for box in sorted(cfg.layout.boxes, key=module_position_key)]

            if key == cfg.keybinds["confirm"] and selected_item is not None:
                # pending_confirm is always None here — same invariant
                # as the global-shortcut tier above.
                should_dismiss, pending_confirm = dispatch_action(action_ctx, ACTION_HANDLERS, selected_item, cfg)
                do_apply_reselect()
                if should_dismiss:
                    dismissed = True
                    provider.dismiss_self()

            elif key in next_item_keys and ordered:
                if sessions_mode.is_expanded():
                    # Level 2 is a deliberate exception to the app-wide
                    # "Tab never wraps within a module, it rolls into
                    # the next one" rule — see same_row_neighbor's
                    # wrap param docstring. Escape/picking an action
                    # are the only ways out (see the dedicated Escape
                    # branch and the per-frame auto-collapse check
                    # above), so Tab/Shift+Tab/Left/Right all just
                    # cycle LOAD/SAVE/DEL/NAME in place instead.
                    next_item = same_row_neighbor(ordered, selected_id, direction=1, wrap=True)
                else:
                    # next_item_across_modules keeps walking forward past
                    # module boundaries until it finds one with an actual
                    # item — a single next-module lookup isn't enough, since
                    # modules with zero nav items (launcher, preview, clock)
                    # are common, not a rare edge case. See its docstring:
                    # found live, this used to leave Tab permanently stuck
                    # the moment selection reached Power Menu's last item.
                    next_item = next_item_across_modules(ordered, module_names, active_module, selected_id)
                if next_item is not None:
                    selected_id, active_module, focus_id = resolve_selection(next_item, focus_id)
            elif key in prev_item_keys and ordered:
                if sessions_mode.is_expanded():
                    prev_item = same_row_neighbor(ordered, selected_id, direction=-1, wrap=True)
                else:
                    prev_item = prev_item_across_modules(ordered, module_names, active_module, selected_id)
                    if (
                        prev_item is not None
                        and active_module != "sessions"
                        and module_of_item(prev_item) == "sessions"
                    ):
                        # Sessions is a deliberate exception to
                        # prev_item_across_modules' usual "rolling
                        # backward lands on the module's LAST item"
                        # feel (right for most modules — see its
                        # docstring) — always slot 1 instead, regardless
                        # of which direction you entered from. Without
                        # this, Shift+Tab-ing in from whatever module
                        # follows Sessions lands on slot 3, one Tab away
                        # from immediately rolling back out again —
                        # found live, reported as "annoying".
                        prev_item = first_item_in_module(ordered, "sessions")
                if prev_item is not None:
                    selected_id, active_module, focus_id = resolve_selection(prev_item, focus_id)
            elif key in module_next_keys:
                # same_row_neighbor first: a module with multiple items
                # on one row (e.g. sessions.py's expanded LOAD/SAVE/DEL/
                # NAME) gets to step across them before Right falls back
                # to its usual jump-to-next-module meaning — see its own
                # docstring. None for the overwhelmingly common single-
                # column module, so this is a no-op there. wrap=True for
                # the same reason as next_item_keys above — level 2 never
                # falls through to a module jump on Left/Right either.
                neighbor = same_row_neighbor(ordered, selected_id, direction=1, wrap=sessions_mode.is_expanded())
                if neighbor is not None:
                    selected_id, active_module, focus_id = resolve_selection(neighbor, focus_id)
                else:
                    next_name = next_module_name(module_names, active_module)
                    if next_name is not None:
                        active_module = next_name
                        first_item = first_item_in_module(ordered, active_module)
                        if first_item is not None:
                            selected_id, active_module, focus_id = resolve_selection(first_item, focus_id)
            elif key in module_prev_keys:
                neighbor = same_row_neighbor(ordered, selected_id, direction=-1, wrap=sessions_mode.is_expanded())
                if neighbor is not None:
                    selected_id, active_module, focus_id = resolve_selection(neighbor, focus_id)
                else:
                    prev_name = prev_module_name(module_names, active_module)
                    if prev_name is not None:
                        active_module = prev_name
                        first_item = first_item_in_module(ordered, active_module)
                        if first_item is not None:
                            selected_id, active_module, focus_id = resolve_selection(first_item, focus_id)
            elif key == cfg.keybinds["spawn_box"]:
                do_spawn_picker()
            elif key == cfg.keybinds["resize"] and active_module is not None:
                do_enter_resize()
            elif key == cfg.keybinds["save_layout"]:
                do_save_layout()
            elif key == cfg.keybinds["cycle_preset"]:
                do_cycle_preset()
            elif key == cfg.keybinds["help"]:
                do_enter_help()
            elif cfg.vim_mode and not resize.active and key == cfg.keybinds["insert"]:
                launcher_mode.enter_typing_mode(launcher, selected_id, active_module)
                active_module = "launcher"
            elif not cfg.vim_mode and not resize.active and 32 <= key <= 126:
                launcher_mode.enter_typing_mode(launcher, selected_id, active_module, chr(key))
                active_module = "launcher"
            elif key == 27 and sessions_mode.is_expanded():
                # Escape collapses an expanded session slot back to
                # browsing all three rows, same "one level at a time"
                # idea as resize_mode's browsing/editing split — but
                # unlike that split, this needs no dedicated hijack
                # tier above: Tab/other-module navigation already works
                # unchanged while expanded (nav_items() alone decides
                # what's navigable), so only Escape itself needs
                # special-casing, right here, ahead of the plain
                # top-level Escape (dismiss) below. Reselect the row
                # directly (see collapse()'s docstring) for the same
                # reason do_apply_reselect's reselect_item_id branch
                # exists — the id this had selected a moment ago
                # (an action within the slot) is about to vanish from
                # nav_items() the instant collapse() runs.
                collapsed_slot = sessions_mode.collapse()
                if collapsed_slot is not None:
                    selected_id = f"sessions:row:{collapsed_slot}"
                    active_module = "sessions"
            elif key == 27:  # Escape, no active input claim: dismiss at top level
                if cfg.return_to_origin and origin_region_id is not None:
                    provider.focus_region(origin_region_id)
                dismissed = True
                provider.dismiss_self()
    finally:
        connectivity.stop()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
