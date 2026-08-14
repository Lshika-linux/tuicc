"""Entry point: ties config, provider, layout engine and rendering together.

Comments here favor short, load-bearing warnings over history. Deeper
rationale lives in CLAUDE/NOTES/design-decisions.md and the other
CLAUDE/NOTES/*.md files — check there before re-deriving a "why".
"""

import curses
import sys
import time

import locale
from pathlib import Path

locale.setlocale(locale.LC_ALL, "")

# Relative to main.py itself, not cwd — must work when a WM keybind
# spawns this from an arbitrary directory, not just `cd tuicc && python main.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tuicc.config import (
    save_layout_to_preset,
    save_new_preset,
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
from tuicc.loop_state import LoopState
from tuicc.actions import spawn_detached, handle_pending_confirm, dispatch_action
from tuicc import procmon
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
from tuicc.theme_setup import reassign_theme_pairs
from tuicc import app_setup, resize_mode, help_mode, pending_moves
from tuicc.modules import launcher as launcher_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import media as media_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sidebar as sidebar_mode


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


# Phase 0 of the LoopState migration (CLAUDE/NOTES/design-decisions.md
# #loopstate-migration): these six touch none of main()'s loop-owned
# state, so they flatten to plain module-level functions with no
# LoopState dependency at all — everything they need is an explicit
# param. Still not moved into sessions.py/sysmon.py/connectivity.py:
# sessions_naming needs cfg.session_names/set_session_name, and no
# module in this codebase imports config.py.

def do_enter_resize(resize):
    resize_mode.enter_edit_mode(resize)


def handle_sessions_naming(key, cfg):
    if key == cfg.keybinds["confirm"]:
        result = sessions_mode.apply_naming()
        if result is not None:
            slot, new_name = result
            cfg.session_names[slot] = new_name or f"Slot {slot}"
            set_session_name(slot, new_name)
            return False
        return True
    return sessions_mode.handle_naming_key(key)


def handle_sysmon_nice(key, cfg):
    if key == cfg.keybinds["confirm"]:
        result = sysmon_mode.apply_nice_edit()
        return result is None
    return sysmon_mode.handle_nice_key(key)


# Connectivity's two prompts: entry/resolution are driven by per-frame
# daemon-mailbox polling (see main()'s loop body, near wifi_agent/
# bluez_agent), not purely by keypress — only the actual per-keypress
# handling is a MODE_HANDLERS entry.
def handle_connectivity_passphrase(key, cfg, wifi_agent):
    if connectivity_mode.is_passphrase_waiting():
        return True  # resolved by the per-frame poll, not here — just swallow keys meanwhile
    if connectivity_mode.passphrase_error() is not None:
        connectivity_mode.cancel_passphrase_entry()
        return False
    # has_pending() going False here (not while waiting/erroring, both
    # handled above) means the daemon cancelled it — not a bug.
    if not wifi_agent.mailbox.has_pending():
        connectivity_mode.cancel_passphrase_entry()
        return False
    elif key == cfg.keybinds["confirm"]:
        text = connectivity_mode.apply_passphrase()
        if text is not None:
            wifi_agent.reply_passphrase(text)
            connectivity_mode.mark_passphrase_submitted()
        return True
    elif key == 27:  # Escape
        connectivity_mode.cancel_passphrase_entry()
        wifi_agent.cancel_current()
        return False
    elif not connectivity_mode.handle_passphrase_key(key):
        connectivity_mode.cancel_passphrase_entry()
        return False
    return True


def handle_connectivity_pairing(key, cfg, bluez_agent):
    # Plain yes/no, resolved directly here (same convention as
    # handle_pending_confirm()) rather than a typed-text apply_*() pair.
    if connectivity_mode.is_pairing_waiting():
        return True
    if connectivity_mode.pairing_error() is not None:
        connectivity_mode.cancel_pairing_confirm()
        return False
    if bluez_agent is None or not bluez_agent.mailbox.has_pending():
        connectivity_mode.cancel_pairing_confirm()
        return False
    # confirm_yes OR confirm (Enter) — see handle_pending_confirm()'s
    # own docstring for why.
    elif key == cfg.keybinds["confirm_yes"] or key == cfg.keybinds["confirm"]:
        bluez_agent.reply_pairing(True)
        connectivity_mode.mark_pairing_submitted()
        return True
    elif key == cfg.keybinds["confirm_no"]:
        bluez_agent.reply_pairing(False)
        connectivity_mode.cancel_pairing_confirm()
        return False
    elif key == 27:  # Escape — same as an explicit reject
        bluez_agent.cancel_current()
        connectivity_mode.cancel_pairing_confirm()
        return False
    return True


def any_two_level_module_expanded():
    # sessions/media/sysmon each own a two-level browsing/expanded
    # session — Tab/Shift+Tab/Left/Right's wrap behavior needs to know
    # if ANY is expanded, not just one specifically. Needed no params
    # even before flattening — every name it touches is a module object,
    # never main()-local state.
    return sessions_mode.is_expanded() or media_mode.is_expanded() or sysmon_mode.is_expanded()


# Phase 1 of the LoopState migration: these four's only loop-owned
# dependency was mode_stack, which needed no nonlocal even as a closure
# (see loop_state.py's own docstring) — flattening them now just makes
# that already-true fact explicit in their signatures.

def do_spawn_picker(loop_state, cfg, spawn_picker):
    available = set(MODULES.keys()) - {b.name for b in cfg.layout.boxes}
    resize_mode.open_picker(spawn_picker, available)
    # open_picker() is a no-op when `available` is empty (no modules
    # left to spawn) — only push the claim if it actually opened.
    if spawn_picker.active:
        loop_state.mode_stack.append("spawn_picker")


def do_enter_box_editing(loop_state, resize, box, is_new=False):
    # Centralizes the mode_stack push for entering editing — same
    # single-append-site safety argument as do_enter_help's
    # unconditional append. Both callers (browsing's own confirm
    # branch, and handle_spawn_picker's handoff) stay in sync
    # automatically.
    resize_mode.enter_box_editing(resize, box, is_new=is_new)
    loop_state.mode_stack.append("resize_editing")


def do_enter_help(loop_state, help_state):
    help_mode.enter(help_state)
    loop_state.mode_stack.append("help")


# "help" can push "help_colors" on top of itself; popping lands back on
# the colors page, not fully closed. help_state.active is depth-agnostic
# ("panel showing at all") — draw()'s call site and connectivity_wants_input
# both read it directly; don't touch it here.
def handle_help(key, loop_state, cfg, help_state):
    if help_state.page is None:
        help_mode.select_page(help_state, key)
        if help_state.page is None and key == 27:  # Escape
            help_state.active = False
            return False
        return True
    if help_state.page == "colors":
        if key == cfg.keybinds["up"]:
            help_mode.move_color_index(help_state, -1)
        elif key == cfg.keybinds["down"]:
            help_mode.move_color_index(help_state, 1)
        elif key == cfg.keybinds["confirm"]:
            help_mode.start_color_edit(help_state, get_raw_theme_values())
            loop_state.mode_stack.append("help_colors")
        elif key == 27:  # Escape
            help_state.page = None
        return True
    if key == 27:  # Escape
        help_state.page = None
    return True


# Phase 2: resize_message/resize_message_until (always touched as a
# pair — see loop_state.py). do_save_layout/do_new_preset touched
# nothing else loop-owned, so they flatten fully; do_cycle_preset
# (still a closure in main(), still needs active_module) converts just
# its own resize_message/_until writes.

def do_save_layout(loop_state, cfg, resize):
    save_layout_to_preset(cfg.layout, cfg.preset_number)
    loop_state.resize_message = f"Saved preset {cfg.preset_number}"
    loop_state.resize_message_until = time.monotonic() + 3.0
    resize_mode.exit_edit_mode(resize)


def handle_help_colors(key, loop_state, cfg, help_state):
    # loop_state.theme_pairs reassigned in place (Phase 3), not a
    # closure-local rebind — the color saves to cfg.theme/config.toml
    # correctly but never renders until restart if this line is skipped.
    if key == cfg.keybinds["confirm"]:
        result = help_mode.apply_color_edit(help_state)
        if result is not None:
            role, color, typed_value = result
            cfg.theme[role] = color
            loop_state.theme_pairs = reassign_theme_pairs(cfg.theme)
            set_theme_color(role, typed_value)
            return False
        return True
    if not help_mode.type_color_key(help_state, key):
        return False
    return True


def do_new_preset(loop_state, cfg, resize):
    # Forks the current layout into a new preset slot rather than
    # overwriting the active one (that's do_save_layout). Unlike
    # do_cycle_preset(), cfg.layout itself doesn't change here, so
    # active_module stays valid — no reset needed.
    new_number = save_new_preset(cfg.layout)
    set_active_preset(new_number)
    cfg.preset_number = new_number
    loop_state.resize_message = f"Saved as new preset {new_number}"
    loop_state.resize_message_until = time.monotonic() + 3.0
    resize_mode.exit_edit_mode(resize)


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    # Backends, agents, StatusWorker — everything the loop below needs
    # before it can start. See app_setup.py for why this is split out.
    app = app_setup.build_app()
    cfg = app.cfg
    control_colors = app.control_colors
    provider = app.provider
    wifi_agent = app.wifi_agent
    bluez_agent = app.bluez_agent
    pid_feed = app.pid_feed
    status_worker = app.status_worker
    cava_reader = app.cava_reader
    action_ctx = app.action_ctx

    # Only resize mode's box-editing consumes this — normal navigation
    # doesn't do spatial movement.
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

    # Tab/Shift+Tab (+Down/Up) roll into the next/previous module; Left/
    # Right jump straight to it. vim hjkl duplicate all four, only under vim_mode.
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
    active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None

    # loop_state: every name main()'s loop rebinds/mutates across frames
    # (mode_stack, resize_message/_until, theme_pairs, pending_confirm,
    # dismissed, last_focused_region_id, origin_region_id,
    # expect_focus_reclaim, resolved_pid_cache — see loop_state.py's own
    # docstring for the full field list and why each one lives there
    # instead of a bare local; CLAUDE/NOTES/design-decisions.md
    # #loopstate-migration for the phased migration this landed through).
    # Can't read state.focused_region_id for origin_region_id at dismiss
    # time: it's already tuicc's own region whenever tuicc has WM focus
    # (parse_tree() doesn't filter self out of that field) — tracks the
    # value being *replaced* on each real transition instead.
    loop_state = LoopState(theme_pairs=app.theme_pairs)

    resize = resize_mode.ResizeState()
    spawn_picker = resize_mode.SpawnPickerState()
    help_state = help_mode.HelpState()
    launcher = launcher_mode.LauncherState()
    moves = pending_moves.PendingMovesQueue()

    def do_cycle_preset():
        # resize_message/_until moved to loop_state (Phase 2 of the
        # LoopState migration) — active_module hasn't yet (Phase 5),
        # so this one closure still needs nonlocal for it alone.
        nonlocal active_module
        numbers = available_preset_numbers()
        if numbers:
            idx = numbers.index(cfg.preset_number) if cfg.preset_number in numbers else -1
            next_number = numbers[(idx + 1) % len(numbers)]
            cfg.layout = build_layout_from_preset(next_number)
            set_active_preset(next_number)
            cfg.preset_number = next_number
            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
            loop_state.resize_message = f"preset {next_number}"
            loop_state.resize_message_until = time.monotonic() + 3.0
        resize_mode.exit_edit_mode(resize)

    def do_apply_reselect():
        # See ActionContext.reselect_region_id/reselect_item_id's
        # docstrings — both consumed once, right after any dispatch/
        # confirm site that might have set either.
        nonlocal selected_id, active_module, focus_id
        if action_ctx.reselect_item_id is not None:
            # No lookup against `ordered` — it still reflects last
            # frame's nav_items(), so this id typically isn't in it yet.
            # module_of_item's "modulename:id" convention derives
            # active_module without needing a real NavItem.
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

    def handle_launcher(key):
        nonlocal focus_id, selected_id, active_module
        # Up/Down shift the ambient launch target without leaving
        # typing mode. Left/Right stay with handle_typing_key (they move
        # the selected search result) — arrow keys never collide with
        # typed chars, including vim's own j/k (a separate keybind).
        if key == cfg.keybinds["up"]:
            current = focus_id if focus_id is not None else state.focused_region_id
            focus_id = sidebar_mode.shift_workspace_id(current, cfg.total_workspaces, -1)
            return True
        if key == cfg.keybinds["down"]:
            current = focus_id if focus_id is not None else state.focused_region_id
            focus_id = sidebar_mode.shift_workspace_id(current, cfg.total_workspaces, 1)
            return True
        if key == cfg.keybinds["confirm"]:
            selected = launcher_mode.resolve_selected(launcher)
            if selected is not None:
                cmd, app_id_hint = selected
                known_ids = {w.id for r in state.regions for w in r.windows}
                pid = spawn_detached(cmd, shell_true=False)  # .desktop Exec= is never shell-interpreted
                # Called before the spawned window can map and steal
                # focus/fullscreen — see no_focus_next_window()'s docstring.
                provider.no_focus_next_window(pid)
                # Falls back to whatever's actually focused when no
                # sidebar region is explicitly selected. app_id_hint is
                # only a fallback — the pid tier is tried first.
                pending_moves.queue_launcher_spawn(
                    moves,
                    focus_id if focus_id is not None else state.focused_region_id,
                    known_ids, pid, app_id_hint, time.monotonic(),
                )
                launcher_mode.exit_typing_mode(launcher)
                selected_id = launcher.saved_selected_id
                active_module = launcher.saved_active_module
                return False
            # selected is None (no search results): nothing happens,
            # typing_mode stays True — not an implicit cancel.
            return True
        if not launcher_mode.handle_typing_key(launcher, key, cfg):
            # Escape, or Backspace on an empty query — same restore as
            # the confirm branch above.
            selected_id = launcher.saved_selected_id
            active_module = launcher.saved_active_module
            return False
        return True

    def handle_spawn_picker(key):
        nonlocal active_module
        choice = resize_mode.choose(spawn_picker, key)
        if choice is not None:
            new_box = ModuleBox(name=choice, x=0.4, y=0.4, w=0.2, h=0.2)
            cfg.layout.boxes.append(new_box)
            active_module = choice
            # Handoff: pop "spawn_picker" before pushing "resize_editing" —
            # the generic dispatch's post-call pop would otherwise
            # remove whatever's on top AFTER we push, not our own frame.
            loop_state.mode_stack.pop()
            do_enter_box_editing(loop_state, resize, new_box, is_new=True)
            return True  # stack already correctly arranged
        return False

    # Keyed by the same names as cfg.keybinds/resize_mode.EditKeyResult's
    # own handoff strings — one vocabulary, not two. Change resize_mode's
    # _handoff() call sites and this dict together; nothing else needs
    # to change in sync.
    HANDOFF_TARGETS = {
        "spawn_box": lambda: do_spawn_picker(loop_state, cfg, spawn_picker),
        "resize": lambda: do_enter_resize(resize),
        "save_layout": lambda: do_save_layout(loop_state, cfg, resize),
        "cycle_preset": do_cycle_preset,
        "new_preset": lambda: do_new_preset(loop_state, cfg, resize),
        "help": lambda: do_enter_help(loop_state, help_state),
    }

    def handle_resize_editing(key):
        nonlocal active_module
        result = resize_mode.handle_editing_key(
            resize, key, cfg, active_module, direction_keys, boxes, term_width, term_height
        )
        if result.deleted_name is not None and active_module == result.deleted_name:
            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
        if result.handoff is not None:
            loop_state.mode_stack.pop()
            HANDOFF_TARGETS[result.handoff]()
            return True  # stack already correctly arranged, don't pop again
        return result.still_claiming

    MODE_HANDLERS = {
        "sessions_naming": lambda key: handle_sessions_naming(key, cfg),
        "sysmon_nice": lambda key: handle_sysmon_nice(key, cfg),
        "connectivity_passphrase": lambda key: handle_connectivity_passphrase(key, cfg, wifi_agent),
        "connectivity_pairing": lambda key: handle_connectivity_pairing(key, cfg, bluez_agent),
        "help": lambda key: handle_help(key, loop_state, cfg, help_state),
        "help_colors": lambda key: handle_help_colors(key, loop_state, cfg, help_state),
        "launcher": handle_launcher,
        "spawn_picker": handle_spawn_picker,
        "resize_editing": handle_resize_editing,
    }

    # No `break` below this point — tuicc is a persistent process the
    # WM shows/hides (VISION.md section 2); the only way out is an
    # unhandled exception (in practice Ctrl+C, caught at file bottom).
    try:
        while True:
            # Drives a faster redraw cadence below — the idle 1000ms
            # cadence alone would turn the marquee's smooth 1-char
            # slide into a visible jump.
            marquee_active = media_mode.has_scrolling_content(status_worker.get("media"))

            # Lazy: only run the visualizer while something could show
            # AND the current preset even has a media box for it.
            media_module_present = any(b.name == "media" for b in cfg.layout.boxes)
            media_players = status_worker.get("media") or []
            any_playing = any(p.playback_status == "Playing" for p in media_players)
            if media_module_present and any_playing:
                cava_reader.start()
            else:
                cava_reader.stop()

            # Runs every frame, before getch() reads a key — a live
            # iwd/bluez daemon callback can cancel a passphrase/pairing
            # prompt at any moment, so this claims mode_stack ahead of
            # any keypress-driven claim, not by dispatch order (moot
            # now — one stack, one top). See
            # CLAUDE/NOTES/design-decisions.md#mode-stack-phase-1.
            #
            # Must not close on the Enter keypress itself — iwd/bluez
            # haven't tried the passphrase/pairing yet at that point,
            # only mark_*_submitted() has run (modules/connectivity.py).
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
            # Also fires mid-retry (iwd re-asking RequestPassphrase after
            # a wrong password) — gated to only when actually WAITING on
            # a result, not while still typing, or has_pending() alone
            # would re-fire every frame and wipe out what's being typed.
            if wifi_agent.mailbox.has_pending() and (
                connectivity_wants_input
                or (loop_state.mode_stack[-1] == "connectivity_passphrase" and connectivity_mode.is_passphrase_waiting())
            ):
                request = wifi_agent.mailbox.get_request()
                connectivity_mode.start_passphrase_entry(request.ssid)
                # Guarded: the retry case above reaches here with the
                # frame already pushed — a bare append would duplicate
                # it, and the eventual single pop() wouldn't fully undo that.
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
                # 300, not 1000: draw() reruns unconditionally every
                # frame regardless of cadence, so redrawing more often
                # while idle is free — and a 1s idle cadence would stack
                # atop Domains already polling as fast as 1s.
                else 300
            )
            stdscr.erase()

            term_height, term_width = stdscr.getmaxyx()
            boxes = compute_boxes(cfg.layout, term_width, term_height)
            state = provider.get_state()

            # Publishes this frame's window list for the background
            # "windows" Domain (procmon.py) — must run before this
            # frame's own RenderContext/nav_items below, and sorted the
            # same way sysmon.py's own display sort is, or the lazy
            # pid-resolution below picks a different "visible" set than
            # what's about to be shown.
            windows_this_frame = sysmon_mode.sort_windows_by_drain(
                procmon.flatten_windows(state), known_stats=status_worker.get("windows") or [],
            )
            pid_feed.set(_resolve_visible_pids(
                windows_this_frame, selected_id, loop_state.resolved_pid_cache, provider, cfg.sysmon_visible_slots,
            ))

            if state.focused_region_id is not None and state.focused_region_id != loop_state.last_focused_region_id:
                loop_state.origin_region_id = loop_state.last_focused_region_id
                loop_state.last_focused_region_id = state.focused_region_id
                if loop_state.expect_focus_reclaim:
                    # Self-inflicted (tuicc reclaiming its own focus after
                    # a spawn/restore) — skip the reset below, but still
                    # update origin/last_focused_region_id above.
                    loop_state.expect_focus_reclaim = False
                else:
                    # A real WM-focus transition (dismissed, workspace
                    # switched, resummoned elsewhere) — force a re-sync
                    # via the still_valid recovery block below, or
                    # focus_id/selected_id stay pinned to a stale context
                    # and every launcher spawn keeps targeting it.
                    selected_id = None

            if action_ctx.restore_queue:
                known_ids = {w.id for r in state.regions for w in r.windows}
                pending_moves.promote_restore_queue(moves, provider, action_ctx.restore_queue, known_ids, time.monotonic())

            if moves.entries:
                current_windows = [w for r in state.regions for w in r.windows]
                reclaimed_focus, resolved_target_regions = pending_moves.process(
                    moves, provider, current_windows, loop_state.dismissed, time.monotonic(), cfg.fullscreen_only,
                    own_region_id=loop_state.last_focused_region_id,
                )
                if reclaimed_focus:
                    loop_state.expect_focus_reclaim = True
                if resolved_target_regions and (focus_id is None or focus_id == loop_state.last_focused_region_id):
                    # expect_focus_reclaim suppresses the real-transition
                    # reset while a restore/spawn is resolving, so
                    # focus_id (preview.py's own target) never otherwise
                    # gets to follow where it landed. Only auto-follow
                    # when nothing was deliberately selected elsewhere —
                    # LOAD-BEARING: without this the preview stayed
                    # permanently blank after a session restore.
                    focus_id = resolved_target_regions[-1]

            # A two-level module's expanded state may only be left via
            # Escape or picking an action, never silently by navigating
            # elsewhere — one check per frame catches every way
            # active_module can change, instead of patching each site.
            if active_module != "sessions" and sessions_mode.is_expanded():
                sessions_mode.collapse()
            if active_module != "media" and media_mode.is_expanded():
                media_mode.collapse()
            if active_module != "sysmon" and sysmon_mode.is_expanded():
                sysmon_mode.collapse()

            ctx = RenderContext(
                state=state,
                selected_id=selected_id,
                focus_id=focus_id,
                theme=loop_state.theme_pairs,
                config=cfg,
                pending_confirm=loop_state.pending_confirm,
                active_module=active_module,
                typing_mode=launcher.typing_mode,
                search_query=launcher.search_query,
                search_selected_index=launcher.search_selected_index,
                wifi_networks=status_worker.get("wifi"),
                bluetooth_devices=status_worker.get("bluetooth"),
                # Poll failure (backend unreachable) takes priority over
                # an agent registration failure — the more complete outage.
                wifi_error=status_worker.get_error("wifi") or wifi_agent.get_error(),
                bluetooth_error=status_worker.get_error("bluetooth") or (bluez_agent.get_error() if bluez_agent else None),
                status=status_worker,
                session_preview=sessions_mode.expanded_preview(),
                control_colors=control_colors,
                cava=cava_reader,
            )

            items = collect_nav_items(cfg.layout, boxes, ctx)
            ordered = tab_order(items, mode=cfg.tab_order)

            still_valid = any(item.id == selected_id for item in ordered)
            # LOAD-BEARING: a Left/Right jump onto an empty module sets
            # selected_id=None on purpose. Without this check, still_valid
            # is False for that same reason and the recovery below
            # immediately undoes the jump on the very same frame.
            intentionally_unselected = selected_id is None and not any(
                module_of_item(item) == active_module for item in ordered
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
                    # selected_id-only update — otherwise focus_id/
                    # active_module can drift out of sync with it (found
                    # live: preview kept showing a stale workspace).
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
                    stdscr, term_width, term_height, loop_state.theme_pairs, help_state,
                    get_raw_navigation_keys(), get_raw_power_menu_actions(), get_raw_theme_values(),
                )
            else:
                draw_all(stdscr, cfg.layout, boxes, ctx)

                if resize.editing and active_module in boxes:
                    resize_mode.draw_editing_highlight(stdscr, boxes[active_module], loop_state.theme_pairs)

                if spawn_picker.active:
                    draw_status_line(stdscr, term_width, resize_mode.spawn_hint_text(spawn_picker), loop_state.theme_pairs.get("urgent", 0))
                elif resize.active:
                    draw_status_line(stdscr, term_width, resize_mode.hint_text(resize, active_module), loop_state.theme_pairs.get("urgent", 0))
                elif loop_state.resize_message is not None:
                    if time.monotonic() < loop_state.resize_message_until:
                        draw_status_line(stdscr, term_width, loop_state.resize_message, loop_state.theme_pairs.get("accent", 0))
                    else:
                        loop_state.resize_message = None

            stdscr.refresh()

            key = stdscr.getch()

            if key == -1:
                continue
            loop_state.dismissed = False

            if loop_state.pending_confirm is not None:
                should_dismiss, loop_state.pending_confirm = handle_pending_confirm(action_ctx, loop_state.pending_confirm, key, cfg)
                do_apply_reselect()
                if should_dismiss:
                    loop_state.dismissed = True
                    provider.dismiss_self()
                continue

            global_item = global_shortcut_item(cfg.global_shortcuts, key)
            if global_item is not None:
                should_dismiss, loop_state.pending_confirm = dispatch_action(action_ctx, ACTION_HANDLERS, global_item, cfg)
                do_apply_reselect()
                if should_dismiss:
                    loop_state.dismissed = True
                    provider.dismiss_self()
                continue

            if loop_state.mode_stack[-1] != "normal":
                still_claiming = MODE_HANDLERS[loop_state.mode_stack[-1]](key)
                if not still_claiming:
                    loop_state.mode_stack.pop()
                continue

            # Browsing: session open, no module being resized/moved —
            # everything except confirm/delete_box/Escape falls through
            # to normal dispatch below, so Tab/arrows/F-keys keep working.
            if resize.active and not resize.editing:
                if resize.confirm_delete:
                    # confirm_yes OR confirm (Enter) — see
                    # handle_pending_confirm()'s own docstring for why.
                    if key == cfg.keybinds["confirm_yes"] or key == cfg.keybinds["confirm"]:
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
                        do_enter_box_editing(loop_state, resize, box)
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

            # Sorted by position, not declaration order — same sort key
            # tab_order() uses for items within a module.
            module_position_key = (
                (lambda box: (box.y, box.x)) if cfg.tab_order == "rows_first"
                else (lambda box: (box.x, box.y))
            )
            module_names = [box.name for box in sorted(cfg.layout.boxes, key=module_position_key)]

            if key == cfg.keybinds["confirm"] and selected_item is not None:
                should_dismiss, loop_state.pending_confirm = dispatch_action(action_ctx, ACTION_HANDLERS, selected_item, cfg)
                do_apply_reselect()
                # sessions.py's/sysmon.py's own "name"/NICE actions call
                # start_naming()/start_nice_edit() on themselves —
                # main.py notices right after dispatch and claims the
                # stack on their behalf.
                if sessions_mode.is_naming():
                    loop_state.mode_stack.append("sessions_naming")
                if sysmon_mode.is_editing_nice():
                    loop_state.mode_stack.append("sysmon_nice")
                if should_dismiss:
                    loop_state.dismissed = True
                    provider.dismiss_self()

            elif key in next_item_keys and ordered:
                if any_two_level_module_expanded():
                    # Level 2 exception to "Tab never wraps, it rolls
                    # into the next module" — Escape/picking an action
                    # are the only ways out (see same_row_neighbor).
                    next_item = same_row_neighbor(ordered, selected_id, direction=1, wrap=True)
                else:
                    # Walks forward past module boundaries until an
                    # actual item is found — zero-item modules (launcher,
                    # preview, clock) are common, one lookup isn't enough.
                    next_item = next_item_across_modules(ordered, module_names, active_module, selected_id)
                if next_item is not None:
                    selected_id, active_module, focus_id = resolve_selection(next_item, focus_id)
            elif key in prev_item_keys and ordered:
                if any_two_level_module_expanded():
                    prev_item = same_row_neighbor(ordered, selected_id, direction=-1, wrap=True)
                else:
                    prev_item = prev_item_across_modules(ordered, module_names, active_module, selected_id)
                    if (
                        prev_item is not None
                        and active_module != "sessions"
                        and module_of_item(prev_item) == "sessions"
                    ):
                        # Sessions exception: always slot 1, not the
                        # module's last item — otherwise Shift+Tab-ing in
                        # lands on slot 3, one Tab from rolling back out.
                        prev_item = first_item_in_module(ordered, "sessions")
                if prev_item is not None:
                    selected_id, active_module, focus_id = resolve_selection(prev_item, focus_id)
            elif key in module_next_keys:
                # same_row_neighbor first: a row with multiple items
                # (e.g. sessions.py's expanded LOAD/SAVE/DEL/NAME) steps
                # across them before Right jumps to the next module.
                # None for the common single-column case — a no-op there.
                neighbor = same_row_neighbor(ordered, selected_id, direction=1, wrap=any_two_level_module_expanded())
                if neighbor is not None:
                    selected_id, active_module, focus_id = resolve_selection(neighbor, focus_id)
                else:
                    next_name = next_module_name(module_names, active_module)
                    if next_name is not None:
                        active_module = next_name
                        first_item = first_item_in_module(ordered, active_module)
                        if first_item is not None:
                            selected_id, active_module, focus_id = resolve_selection(first_item, focus_id)
                        else:
                            # Landed on an empty module (bars, clock,
                            # launcher...) — clear selected_id too, or
                            # the old item stays highlighted in the
                            # module we just left while this one's
                            # border also claims active.
                            selected_id = None
            elif key in module_prev_keys:
                neighbor = same_row_neighbor(ordered, selected_id, direction=-1, wrap=any_two_level_module_expanded())
                if neighbor is not None:
                    selected_id, active_module, focus_id = resolve_selection(neighbor, focus_id)
                else:
                    prev_name = prev_module_name(module_names, active_module)
                    if prev_name is not None:
                        active_module = prev_name
                        first_item = first_item_in_module(ordered, active_module)
                        if first_item is not None:
                            selected_id, active_module, focus_id = resolve_selection(first_item, focus_id)
                        else:
                            # See module_next_keys' matching branch above.
                            selected_id = None
            elif key == cfg.keybinds["spawn_box"]:
                do_spawn_picker(loop_state, cfg, spawn_picker)
            elif key == cfg.keybinds["resize"] and active_module is not None:
                do_enter_resize(resize)
            elif key == cfg.keybinds["save_layout"]:
                do_save_layout(loop_state, cfg, resize)
            elif key == cfg.keybinds["cycle_preset"]:
                do_cycle_preset()
            elif key == cfg.keybinds["new_preset"]:
                do_new_preset(loop_state, cfg, resize)
            elif key == cfg.keybinds["help"]:
                do_enter_help(loop_state, help_state)
            elif cfg.vim_mode and not resize.active and key == cfg.keybinds["insert"]:
                launcher_mode.enter_typing_mode(launcher, selected_id, active_module)
                loop_state.mode_stack.append("launcher")
                active_module = "launcher"
            elif not cfg.vim_mode and not resize.active and 32 <= key <= 126:
                launcher_mode.enter_typing_mode(launcher, selected_id, active_module, chr(key))
                loop_state.mode_stack.append("launcher")
                active_module = "launcher"
            elif key == 27:
                # Escape collapses whichever two-level module is
                # expanded, back to browsing. collapse() is safe to call
                # unconditionally — all three mirror sessions.py's own,
                # returning None as a no-op when nothing was expanded —
                # so trying each in turn needs no is_expanded() guard.
                # All three share the same "module:value:row" id shape.
                for mod_name, collapse_fn in (
                    ("sessions", sessions_mode.collapse),
                    ("media", media_mode.collapse),
                    ("sysmon", sysmon_mode.collapse),
                ):
                    collapsed = collapse_fn()
                    if collapsed is not None:
                        selected_id = f"{mod_name}:{collapsed}:row"
                        active_module = mod_name
                        break
                else:
                    # Nothing was expanded — no active input claim:
                    # dismiss at top level.
                    if cfg.return_to_origin and loop_state.origin_region_id is not None:
                        provider.focus_region(loop_state.origin_region_id)
                    loop_state.dismissed = True
                    provider.dismiss_self()
    finally:
        status_worker.stop()
        cava_reader.stop()
        wifi_agent.stop()
        if bluez_agent is not None:
            bluez_agent.stop()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
