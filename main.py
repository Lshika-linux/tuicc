"""Entry point: ties config, provider, layout engine and rendering together.

Comments here favor short, load-bearing warnings over history. Deeper
rationale lives in CLAUDE/NOTES/design-decisions.md and the other
CLAUDE/NOTES/*.md files — check there before re-deriving a "why".
"""

import curses
import dataclasses
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
    set_theme_colors,
    set_session_name,
    get_raw_theme_values,
    get_raw_navigation_keys,
    get_raw_power_menu_actions,
    build_layout_from_preset,
    pick_preset_for_size,
    available_theme_preset_numbers,
    load_theme_preset,
    save_new_theme_preset,
)
from tuicc.loop_state import LoopState
from tuicc.actions import spawn_detached, handle_pending_confirm, dispatch_action
from tuicc.layout import ModuleBox
from tuicc.navigation import (
    resolve_selection,
    global_shortcut_item,
    next_module_name,
    prev_module_name,
    first_item_in_module,
    next_item_across_modules,
    prev_item_across_modules,
    same_row_neighbor,
    module_of_item,
    LAST_ITEM_QUERY,
)
from tuicc.render import draw_all, ACTION_HANDLERS, MODULES, NAV_PROVIDERS, AUTO_FH_PROVIDERS, apply_auto_fh
from tuicc.render_utils import draw_status_line
from tuicc.theme import resolve_color
from tuicc.theme_setup import reassign_theme_pairs, apply_background, assign_control_toggle_pairs
from tuicc.theme_presets import preset_cycle_list, next_preset
from tuicc import app_setup, frame_update, resize_mode, help_mode, pending_moves
from tuicc.modules import launcher as launcher_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import media as media_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sidebar as sidebar_mode


# Module-level, not closures: touch none of main()'s LoopState fields
# (CLAUDE/NOTES/design-decisions.md#loopstate-migration). Still not
# moved into sessions.py/sysmon.py/connectivity.py — sessions_naming
# needs cfg.session_names/set_session_name, and no module here imports
# config.py.

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


# Level-2 connectivity browsing (see connectivity.py's own "level-2
# browsing" section docstring for why this is a real mode_stack claim,
# not the orthogonal two-level-expand mechanism sessions/media/sysmon
# use) — hand-rolls every key itself, same "no dispatch_action inside
# a claimed modal" shape as handle_connectivity_pairing below and
# resize_mode.handle_editing_key. next_item_keys/prev_item_keys are
# main()'s own already-built sets (Tab/Down and Shift+Tab/Up, plus vim
# hjkl duplicates under vim_mode) — reused as-is so vim keys keep
# working here for free, not re-derived.
def handle_connectivity_browsing(key, loop_state, cfg, status_worker, next_item_keys, prev_item_keys):
    section = connectivity_mode.browsing_section()
    if section is None:
        return False  # safety net — shouldn't happen, see frame_update.py's own auto-exit

    # Forget-confirm: a Y/N sub-state of browsing itself (see
    # connectivity.request_forget()'s own docstring for why this isn't
    # a separate mode_stack tier) — checked first, same structure the
    # resize browsing-level dispatch uses for its own confirm_delete.
    if connectivity_mode.is_confirming_forget():
        ssid = connectivity_mode.confirming_forget_ssid()
        if key == cfg.keybinds["confirm_yes"] or key == cfg.keybinds["confirm"]:
            status_worker.request_action("wifi", "forget", ssid)
            connectivity_mode.cancel_forget()
        elif key == cfg.keybinds["confirm_no"] or key == 27:  # Escape also cancels
            connectivity_mode.cancel_forget()
        return True

    if key == 27:  # Escape
        connectivity_mode.stop_browsing()
        loop_state.selected_id = f"connectivity:{'wifi' if section == 'wifi' else 'bt'}:header"
        return False
    if key == cfg.keybinds["scan"]:
        if section == "wifi":
            status_worker.request_action("wifi", "scan", None)
        else:
            status_worker.request_action("bluetooth", "discover", None)
        connectivity_mode.flash_header_action(section, "scan")
        # Real is_scanning() completes too fast on real hardware to be
        # a reliable "something is happening" signal on its own — see
        # guarantee_scan_blink()'s own docstring.
        connectivity_mode.guarantee_scan_blink(section)
        return True
    # wifi-only keys below — forgetting/hidden-connect are wifi
    # concepts, no bluetooth equivalent was asked for (see this
    # feature's own scoping — impala doesn't cover bluetooth either).
    # Radio power DOES have a bluetooth equivalent (bt_power_toggle,
    # below) — bluetooth just uses its own separate key.
    if section == "wifi" and key == cfg.keybinds["wifi_forget"] and loop_state.selected_id:
        ssid = loop_state.selected_id.split(":", 2)[2]
        networks = status_worker.get("wifi") or []
        if any(n.ssid == ssid for n in networks):
            connectivity_mode.request_forget(ssid)
        return True
    if section == "wifi" and key == cfg.keybinds["wifi_connect_hidden"]:
        connectivity_mode.start_hidden_ssid_entry()
        loop_state.mode_stack.append("connectivity_hidden_ssid")
        return True
    # Re-enabled after a real, live-confirmed fix — see
    # CLAUDE/NOTES/known-limitations.md#wifi-power-toggle-disabled for
    # the full incident and the two root causes it took to actually
    # fix (set_powered() targeting the wrong D-Bus property, then
    # targeting the right one via the wrong path-finder). Verified live
    # on Rafi's own machine, off then back on, no reboot needed.
    # pending_key="power" (not the default arg-as-key) so connectivity.
    # py's own _power_progress_line() only ever has to check ONE key
    # regardless of which direction was requested.
    if section == "wifi" and key == cfg.keybinds["wifi_power_toggle"]:
        adapter = status_worker.get("wifi_adapter")
        if adapter is not None and adapter.powered is not None:
            status_worker.request_action("wifi", "set_powered", not adapter.powered, pending_key="power")
            connectivity_mode.flash_header_action(section, "power")
        return True
    # Bluetooth's own equivalents, added 2026-08-16 mirroring the wifi
    # branch above exactly (same pending_key="power" reasoning) — plus
    # Pairable, which wifi has no equivalent of at all.
    if section == "bluetooth" and key == cfg.keybinds["bt_power_toggle"]:
        adapter = status_worker.get("bluetooth_adapter")
        if adapter is not None and adapter.powered is not None:
            status_worker.request_action("bluetooth", "set_powered", not adapter.powered, pending_key="power")
            connectivity_mode.flash_header_action(section, "power")
        return True
    if section == "bluetooth" and key == cfg.keybinds["bt_pairable_toggle"]:
        adapter = status_worker.get("bluetooth_adapter")
        if adapter is not None and adapter.pairable is not None:
            status_worker.request_action("bluetooth", "set_pairable", not adapter.pairable, pending_key="pairable")
            connectivity_mode.flash_header_action(section, "pairable")
        return True
    items = status_worker.get(section) or []
    if key == cfg.keybinds["confirm"] and items and loop_state.selected_id:
        current_key = loop_state.selected_id.split(":", 2)[2]
        # Guards the one genuinely-possible stale-selection edge case:
        # browsing entered while the section was empty (selected_id is
        # still the header id, not a real item) and an item then showed
        # up on a later poll before any Up/Down/Enter re-anchored
        # selection to a real row — without this, current_key would be
        # "header" itself and toggle_* would fire a bogus connect
        # attempt against a network/device that doesn't exist.
        valid_keys = {n.ssid for n in items} if section == "wifi" else {d.id for d in items}
        if current_key in valid_keys:
            if section == "wifi":
                connectivity_mode.toggle_wifi(status_worker, current_key)
            else:
                connectivity_mode.toggle_bluetooth(status_worker, current_key)
        return True
    if key in next_item_keys and items:
        loop_state.selected_id = connectivity_mode.next_browsing_selection(section, items, loop_state.selected_id, 1)
        return True
    if key in prev_item_keys and items:
        loop_state.selected_id = connectivity_mode.next_browsing_selection(section, items, loop_state.selected_id, -1)
        return True
    return True


def handle_connectivity_hidden_ssid(key, loop_state, cfg, status_worker):
    if key == cfg.keybinds["confirm"]:
        ssid = connectivity_mode.apply_hidden_ssid()
        connectivity_mode.cancel_hidden_ssid_entry()
        if ssid:  # empty submit is a silent cancel — see apply_hidden_ssid()'s own docstring
            status_worker.request_action("wifi", "connect_hidden", ssid)
        return False
    if not connectivity_mode.handle_hidden_ssid_key(key):
        connectivity_mode.cancel_hidden_ssid_entry()
        return False
    return True


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
    elif key == cfg.keybinds["wifi_passphrase_visibility_toggle"]:
        connectivity_mode.toggle_passphrase_visibility()
        return True
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


# mode_stack was these four's only shared dependency, and never needed
# nonlocal even as a closure (see loop_state.py's own docstring) —
# module-level just makes that explicit in the signature.

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


# Called from two places: F1's Colors page (cfg.keybinds["cycle_preset"],
# reusing that key the same way resize mode's own F4 means "next
# layout preset" there) AND cfg.keybinds["cycle_theme_preset"] (F7,
# its own dedicated key — see defaults/config.toml's own comment for
# why it isn't a third cycle_preset context) firing from anywhere else
# in the app. Sets BOTH feedback channels every time rather than
# branching on which caller fired it: help_state.color_message (shown
# by the Colors page itself, the only place that's visible while
# help_state.active — see color_role_lines' own docstring) and
# loop_state.resize_message (the generic status-line toast, shown
# everywhere else — see its own "generic despite its name" comment at
# the draw call site). Whichever one the current screen actually
# renders is the one the user sees; the other just sits unused for
# that frame. See theme_presets.py's own module docstring for why no
# "active theme preset" is tracked anywhere — next_preset() re-derives
# "where am I now" from the live config.toml values on every press.
def do_cycle_theme_preset(loop_state, cfg, help_state, stdscr, app):
    user_presets = {n: load_theme_preset(n) for n in available_theme_preset_numbers()}
    cycle_list = preset_cycle_list(user_presets)
    result = next_preset(get_raw_theme_values(), cycle_list)
    if result is None:
        return
    name, values = result
    for role, value in values.items():
        cfg.theme[role] = resolve_color(value)
    loop_state.theme_pairs = reassign_theme_pairs(cfg.theme)
    apply_background(stdscr, loop_state.theme_pairs)
    # control.toggle state colors (Performance Mode's dot, etc.) are a
    # SEPARATE curses-pair range, built once at startup (app_setup.py)
    # and never touched again — found live, reported directly: a
    # toggle's own colored dot kept the OLD background baked into its
    # pair after cycling here, the one spot this whole fix missed the
    # first time. frame_update.py reads app.control_colors fresh every
    # frame straight off this same `app` object, so reassigning it here
    # is picked up on the very next frame, no extra plumbing needed.
    app.control_colors = assign_control_toggle_pairs(
        cfg.control_toggles, len(loop_state.theme_pairs) + 1, cfg.theme.get("background", -1)
    )
    set_theme_colors(values)
    help_state.color_message = f"Applied {name}"
    loop_state.resize_message = f"Applied {name}"
    loop_state.resize_message_until = time.monotonic() + 3.0
    loop_state.resize_message_urgent = False


def do_save_theme_preset(loop_state, cfg, help_state):
    new_number = save_new_theme_preset(get_raw_theme_values())
    help_state.color_message = f"Saved as new preset {new_number}"


# "help" can push "help_colors" on top of itself; popping lands back on
# the colors page, not fully closed. help_state.active is depth-agnostic
# ("panel showing at all") — draw()'s call site and connectivity_wants_input
# both read it directly; don't touch it here.
def handle_help(key, loop_state, cfg, help_state, stdscr, app):
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
        elif key == cfg.keybinds["cycle_preset"]:
            do_cycle_theme_preset(loop_state, cfg, help_state, stdscr, app)
        elif key == cfg.keybinds["new_preset"]:
            do_save_theme_preset(loop_state, cfg, help_state)
        elif key == 27:  # Escape
            help_state.page = None
        return True
    if key == 27:  # Escape
        help_state.page = None
    return True


# resize_message/resize_message_until always move together — see
# loop_state.py.

def do_save_layout(loop_state, cfg, resize):
    save_layout_to_preset(cfg.layout, cfg.preset_number)
    loop_state.resize_message = f"Saved preset {cfg.preset_number}"
    loop_state.resize_message_until = time.monotonic() + 3.0
    loop_state.resize_message_urgent = False
    resize_mode.exit_edit_mode(resize)


def handle_help_colors(key, loop_state, cfg, help_state, stdscr, app):
    # loop_state.theme_pairs reassigned in place, not just cfg.theme —
    # skip this and the color saves to config.toml but never renders
    # until restart.
    if key == cfg.keybinds["confirm"]:
        result = help_mode.apply_color_edit(help_state)
        if result is not None:
            role, color, typed_value = result
            cfg.theme[role] = color
            loop_state.theme_pairs = reassign_theme_pairs(cfg.theme)
            # role == "background" is the only edit that actually
            # changes anything here (assign_control_toggle_pairs' own
            # bg_color param is cfg.theme's "background" role and
            # nothing else) — cheaper to just always re-apply both than
            # special-case which single role was just edited.
            apply_background(stdscr, loop_state.theme_pairs)
            app.control_colors = assign_control_toggle_pairs(
                cfg.control_toggles, len(loop_state.theme_pairs) + 1, cfg.theme.get("background", -1)
            )
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
    loop_state.resize_message_urgent = False
    resize_mode.exit_edit_mode(resize)


# active_module/selected_id/focus_id always move together —
# resolve_selection() returns all three as one unit at 8+ call sites.

def do_cycle_preset(loop_state, cfg, resize):
    numbers = available_preset_numbers()
    if numbers:
        idx = numbers.index(cfg.preset_number) if cfg.preset_number in numbers else -1
        next_number = numbers[(idx + 1) % len(numbers)]
        cfg.layout = build_layout_from_preset(next_number)
        # Same reasoning as build_app()'s own call — a freshly loaded
        # preset can carry fh_auto boxes needing (re)computation, and
        # cfg is already fully built here (a live running app switching
        # presets), unlike load_config()'s own bootstrapping order.
        apply_auto_fh(cfg.layout, cfg)
        set_active_preset(next_number)
        cfg.preset_number = next_number
        loop_state.active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
        loop_state.resize_message = f"preset {next_number}"
        loop_state.resize_message_until = time.monotonic() + 3.0
        loop_state.resize_message_urgent = False
    resize_mode.exit_edit_mode(resize)


def do_apply_reselect(loop_state, action_ctx, ordered):
    # See ActionContext.reselect_region_id/reselect_item_id's docstrings
    # — both consumed once, right after any dispatch/confirm site that
    # might have set either. ordered is a per-frame value (this frame's
    # tab_order() result), passed explicitly rather than closure-read,
    # same as boxes/term_width/term_height elsewhere in this file.
    if action_ctx.reselect_item_id is not None:
        # No lookup against `ordered` — it still reflects last frame's
        # nav_items(), so this id typically isn't in it yet.
        # module_of_item's "modulename:id" convention derives
        # active_module without needing a real NavItem.
        loop_state.selected_id = action_ctx.reselect_item_id
        loop_state.active_module = loop_state.selected_id.split(":")[0]
        action_ctx.reselect_item_id = None
        return
    # Looks up a real region NavItem instead of hardcoding an id-prefix
    # convention here, since which module actually owns "region" items
    # (sidebar vs sidebar_compact) is a preset/config choice, not
    # something main.py should assume.
    if action_ctx.reselect_region_id is None:
        return
    region_item = next(
        (it for it in ordered if it.target_kind == "region" and it.focus_target == action_ctx.reselect_region_id),
        None,
    )
    if region_item is not None:
        loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(
            region_item, loop_state.focus_id
        )
    action_ctx.reselect_region_id = None


def do_apply_toast(loop_state, action_ctx):
    # See ActionContext.toast_message's own docstring — same "handler
    # writes an output field on ctx, main.py drains it right after the
    # dispatch call that may have set it, single-use" idiom as
    # do_apply_reselect above. Reuses the same generic toast mechanism
    # save/cycle-preset already do (loop_state.py's own comment on why
    # resize_message is generic despite its name).
    if action_ctx.toast_message is None:
        return
    loop_state.resize_message = action_ctx.toast_message
    loop_state.resize_message_until = time.monotonic() + 3.0
    loop_state.resize_message_urgent = action_ctx.toast_urgent
    action_ctx.toast_message = None
    action_ctx.toast_urgent = False


def _apply_launcher_routing_default(loop_state, launcher, app):
    """GitHub issue #9's routing-rule follow-on. Forces focus_id to
    whatever routed_target() says every keystroke, overwriting any
    stale sticky value it already had — focus_id is sticky app-wide
    (resolve_selection() only updates it for region items, so it keeps
    showing the last real workspace pick across unrelated modules), so
    "only touch it if it's still None" doesn't work: it's essentially
    never None by the time typing starts.

    Stops for whichever app is CURRENTLY selected once the user
    presses Up/Down while it's selected (see
    LauncherState.manual_target_app_id's own docstring) — their own
    pick wins for that app specifically, not for the rest of this
    typing session as a whole. Live-found: arrowing away from one app
    (with no rule, or a rule you don't want right now) must not
    silently suppress a DIFFERENT app's own rule once the selection
    moves on to it — typing further, or Left/Right, can change which
    app is selected without the user ever touching Up/Down again for
    THAT app.

    When routed_target() comes back None (no rule for the currently
    selected app, the overwhelmingly common case), focus_id reverts to
    launcher.pre_routing_focus_id — the real target as it stood right
    before typing started, not just left alone. Live-found needed
    twice over: forcing it to None broke the launcher's ordinary "show
    where this would launch" default entirely (see git history); doing
    nothing instead left focus_id stuck on an earlier rule's target
    forever once the selection moved on to an unruled app, even though
    nothing about the current selection has anything to do with that
    rule anymore.
    """
    selected = launcher_mode.resolve_selected(launcher)
    app_id_hint = selected[1] if selected else None
    if app_id_hint is not None and app_id_hint == launcher.manual_target_app_id:
        return
    routed = launcher_mode.routed_target(launcher, app.wm_config)
    loop_state.focus_id = routed if routed is not None else launcher.pre_routing_focus_id


def handle_launcher(key, loop_state, cfg, state, launcher, provider, moves, app):
    # Up/Down shift the ambient launch target without leaving typing
    # mode. Left/Right stay with handle_typing_key (they move the
    # selected search result) — arrow keys never collide with typed
    # chars, including vim's own j/k (a separate keybind).
    if key == cfg.keybinds["up"]:
        current = loop_state.focus_id if loop_state.focus_id is not None else state.focused_region_id
        ids = sidebar_mode.slot_ids(
            state.regions, app.wm_config, cfg.total_workspaces,
            cfg.workspace_mode, cfg.workspace_names,
        )
        loop_state.focus_id = sidebar_mode.shift_workspace_id(current, ids, -1)
        selected = launcher_mode.resolve_selected(launcher)
        launcher.manual_target_app_id = selected[1] if selected else None
        return True
    if key == cfg.keybinds["down"]:
        current = loop_state.focus_id if loop_state.focus_id is not None else state.focused_region_id
        ids = sidebar_mode.slot_ids(
            state.regions, app.wm_config, cfg.total_workspaces,
            cfg.workspace_mode, cfg.workspace_names,
        )
        loop_state.focus_id = sidebar_mode.shift_workspace_id(current, ids, 1)
        selected = launcher_mode.resolve_selected(launcher)
        launcher.manual_target_app_id = selected[1] if selected else None
        return True
    if key == cfg.keybinds["confirm"]:
        selected = launcher_mode.resolve_selected(launcher)
        if selected is not None:
            cmd, app_id_hint = selected
            known_ids = {w.id for r in state.regions for w in r.windows}
            # log_path captures stdout+stderr the same way
            # promote_restore_queue() already does for restore spawns —
            # gives a fast nonzero-exit failure toast (pending_moves.py's
            # _quick_exit_failure_message) real output to point at.
            log_path = pending_moves.SPAWN_LOG_DIR / f"launcher_{app_id_hint or 'unknown'}_{int(time.time())}.log"
            pid = spawn_detached(cmd, shell_true=False, log_path=log_path)  # .desktop Exec= is never shell-interpreted
            # Called before the spawned window can map and steal
            # focus/fullscreen — see no_focus_next_window()'s docstring.
            provider.no_focus_next_window(pid)
            # Falls back to whatever's actually focused when no sidebar
            # region is explicitly selected. app_id_hint is only a
            # fallback — the pid tier is tried first.
            pending_moves.queue_launcher_spawn(
                moves,
                loop_state.focus_id if loop_state.focus_id is not None else state.focused_region_id,
                known_ids, pid, app_id_hint, time.monotonic(), log_path,
            )
            launcher_mode.exit_typing_mode(launcher)
            loop_state.selected_id = launcher.saved_selected_id
            loop_state.active_module = launcher.saved_active_module
            return False
        # selected is None (no search results): nothing happens,
        # typing_mode stays True — not an implicit cancel.
        return True
    if not launcher_mode.handle_typing_key(launcher, key, cfg):
        # Escape, or Backspace on an empty query — same restore as the
        # confirm branch above.
        loop_state.selected_id = launcher.saved_selected_id
        loop_state.active_module = launcher.saved_active_module
        return False
    _apply_launcher_routing_default(loop_state, launcher, app)
    return True


def handle_spawn_picker(key, loop_state, cfg, spawn_picker, resize):
    choice = resize_mode.choose(spawn_picker, key)
    if choice is not None:
        # A module with an AUTO_FH_PROVIDERS entry spawns already sized
        # to fit its own real content (fh_auto=True — see layout.py's
        # own ModuleBox.fh_auto docstring), not the generic h=0.2 ratio
        # every other module still gets.
        if choice in AUTO_FH_PROVIDERS:
            new_box = ModuleBox(name=choice, x=0.4, y=0.4, w=0.2, h=None,
                                 fh=AUTO_FH_PROVIDERS[choice](cfg), fh_auto=True)
        else:
            new_box = ModuleBox(name=choice, x=0.4, y=0.4, w=0.2, h=0.2)
        cfg.layout.boxes.append(new_box)
        loop_state.active_module = choice
        # Handoff: pop "spawn_picker" before pushing "resize_editing" —
        # the generic dispatch's post-call pop would otherwise remove
        # whatever's on top AFTER we push, not our own frame.
        loop_state.mode_stack.pop()
        do_enter_box_editing(loop_state, resize, new_box, is_new=True)
        return True  # stack already correctly arranged
    return False


def handle_resize_editing(key, loop_state, resize, cfg, direction_keys, boxes, term_width, term_height, handoff_targets):
    result = resize_mode.handle_editing_key(
        resize, key, cfg, loop_state.active_module, direction_keys, boxes, term_width, term_height
    )
    if result.deleted_name is not None and loop_state.active_module == result.deleted_name:
        loop_state.active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
    if result.handoff is not None:
        loop_state.mode_stack.pop()
        handoff_targets[result.handoff]()
        return True  # stack already correctly arranged, don't pop again
    return result.still_claiming


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    # Two machines can report the identical pixel resolution and still
    # hand curses a completely different cell grid (font size, DPI/
    # output scale) — pick_preset_for_size() lets a preset opt in
    # (its own [preset] min_cols/min_rows) to being auto-selected for
    # a grid this size, session-only, never persisted to config.toml.
    # See CLAUDE/NOTES/design-decisions.md#preset-auto-select-by-size.
    startup_term_height, startup_term_width = stdscr.getmaxyx()
    preset_override = pick_preset_for_size(startup_term_width, startup_term_height)

    # Backends, agents, StatusWorker — everything the loop below needs
    # before it can start. See app_setup.py for why this is split out.
    app = app_setup.build_app(preset_override=preset_override)
    cfg = app.cfg
    # No local control_colors var — frame_update.py reads app.control_colors
    # straight off this same `app` object every frame, so a live theme
    # change (do_cycle_theme_preset/handle_help_colors reassigning
    # app.control_colors in place) is picked up automatically; a local
    # copy here would silently go stale the moment that happens.
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

    # Every name main()'s loop rebinds/mutates across frames — see
    # loop_state.py for the full field list and rationale. Can't read
    # state.focused_region_id for origin_region_id at dismiss time: it's
    # already tuicc's own region whenever tuicc has WM focus
    # (parse_tree() doesn't filter self out of that field) — tracks the
    # value being *replaced* on each real transition instead.
    loop_state = LoopState(
        theme_pairs=app.theme_pairs,
        active_module=cfg.layout.boxes[0].name if cfg.layout.boxes else None,
    )
    # build_app() stays deliberately stdscr-free (see its own docstring)
    # — the "background" role's own fill can only be applied here, the
    # first point main() actually has both theme_pairs and stdscr
    # together. See apply_background()'s own docstring.
    apply_background(stdscr, loop_state.theme_pairs)

    resize = resize_mode.ResizeState()
    spawn_picker = resize_mode.SpawnPickerState()
    help_state = help_mode.HelpState()
    launcher = launcher_mode.LauncherState()
    moves = pending_moves.PendingMovesQueue()

    # Keyed by the same names as cfg.keybinds/resize_mode.EditKeyResult's
    # own handoff strings — one vocabulary, not two. Change resize_mode's
    # _handoff() call sites and this dict together; nothing else needs
    # to change in sync.
    HANDOFF_TARGETS = {
        "spawn_box": lambda: do_spawn_picker(loop_state, cfg, spawn_picker),
        "resize": lambda: do_enter_resize(resize),
        "save_layout": lambda: do_save_layout(loop_state, cfg, resize),
        "cycle_preset": lambda: do_cycle_preset(loop_state, cfg, resize),
        "new_preset": lambda: do_new_preset(loop_state, cfg, resize),
        "help": lambda: do_enter_help(loop_state, help_state),
    }

    MODE_HANDLERS = {
        "sessions_naming": lambda key: handle_sessions_naming(key, cfg),
        "sysmon_nice": lambda key: handle_sysmon_nice(key, cfg),
        "connectivity_passphrase": lambda key: handle_connectivity_passphrase(key, cfg, wifi_agent),
        "connectivity_pairing": lambda key: handle_connectivity_pairing(key, cfg, bluez_agent),
        "connectivity_browsing": lambda key: handle_connectivity_browsing(
            key, loop_state, cfg, status_worker, next_item_keys, prev_item_keys
        ),
        "connectivity_hidden_ssid": lambda key: handle_connectivity_hidden_ssid(key, loop_state, cfg, status_worker),
        "help": lambda key: handle_help(key, loop_state, cfg, help_state, stdscr, app),
        "help_colors": lambda key: handle_help_colors(key, loop_state, cfg, help_state, stdscr, app),
        "launcher": lambda key: handle_launcher(key, loop_state, cfg, state, launcher, provider, moves, app),
        "spawn_picker": lambda key: handle_spawn_picker(key, loop_state, cfg, spawn_picker, resize),
        "resize_editing": lambda key: handle_resize_editing(
            key, loop_state, resize, cfg, direction_keys, boxes, term_width, term_height, HANDOFF_TARGETS
        ),
    }

    # No `break` below this point — tuicc is a persistent process the
    # WM shows/hides (VISION.md section 2); the only way out is an
    # unhandled exception (in practice Ctrl+C, caught at file bottom).
    try:
        while True:
            frame = frame_update.update_frame(stdscr, app, loop_state, resize, spawn_picker, help_state, launcher, moves)
            ctx = frame.ctx
            boxes = frame.boxes
            term_width = frame.term_width
            term_height = frame.term_height
            ordered = frame.ordered
            selected_item = frame.selected_item
            state = ctx.state

            if help_state.active:
                help_mode.draw(
                    stdscr, term_width, term_height, loop_state.theme_pairs, help_state,
                    get_raw_navigation_keys(), get_raw_power_menu_actions(), get_raw_theme_values(),
                )
            else:
                draw_all(stdscr, cfg.layout, boxes, ctx)

                # Redraws sidebar's own border a second time, after
                # every module's normal draw() has already run — see
                # sidebar.draw_hidden_indicators()'s own docstring for
                # why this specific box's "+N hidden" indicator has to
                # be the literal last thing drawn on its top/bottom
                # rows this frame, not merely correctly drawn at some
                # point during it.
                if "sidebar" in boxes and boxes["sidebar"][2] > 0 and boxes["sidebar"][3] > 0:
                    sidebar_mode.draw_hidden_indicators(stdscr, boxes["sidebar"], ctx, "sidebar")

                if resize.editing and loop_state.active_module in boxes:
                    resize_mode.draw_editing_highlight(stdscr, boxes[loop_state.active_module], loop_state.theme_pairs)

                if spawn_picker.active:
                    draw_status_line(stdscr, term_width, resize_mode.spawn_hint_text(spawn_picker), loop_state.theme_pairs.get("urgent", 0))
                elif resize.active:
                    draw_status_line(stdscr, term_width, resize_mode.hint_text(resize, loop_state.active_module), loop_state.theme_pairs.get("urgent", 0))
                elif loop_state.resize_message is not None:
                    if time.monotonic() < loop_state.resize_message_until:
                        color_role = "urgent" if loop_state.resize_message_urgent else "accent"
                        draw_status_line(stdscr, term_width, loop_state.resize_message, loop_state.theme_pairs.get(color_role, 0))
                    else:
                        loop_state.resize_message = None
                        loop_state.resize_message_urgent = False

                # stdscr.redrawln(beg, num) marks those specific screen
                # lines as corrupted, forcing curses to fully retransmit
                # their content on the NEXT refresh() instead of trusting
                # its own per-cell diff — see
                # CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption's
                # final entries: a wide/VS16 glyph followed, on a later
                # frame, by narrower content in the shared preview.py box
                # left a stray leftover glyph fragment on screen. stdscr.
                # instr() proved curses' own internal buffer was already
                # correct — the terminal just never received fresh bytes
                # for that one cell, because curses' diff believed nothing
                # had changed there. stdscr.clearok(True) (tried first)
                # also fixed this, but sends the terminal an actual
                # clear-screen capability, which visibly flashed blank on
                # every armed frame — confirmed unacceptable live, twice
                # over (first armed every frame, then only on this exact
                # transition — even the occasional flash was rejected).
                # redrawln() is a materially different, cheaper primitive:
                # it never sends a clear-screen command, only forces a
                # normal cursor-positioned rewrite of the affected lines'
                # full width — the same kind of terminal operation any
                # ordinary content update already uses without flicker.
                # touchline() (same "mark dirty" idea, curses' more
                # general draw-optimization bookkeeping call) was tried
                # first and did NOT fix the corruption — redrawln() is the
                # more specific "these lines are corrupted, redraw them
                # completely" primitive, and only that one actually works.
                # Scoped to just the preview box's own interior rows (not
                # the whole terminal, unlike clearok) — still gated on the
                # box's line count actually changing, since that's the
                # one confirmed corrupting transition and there's no
                # reason to force even a cheap resend on frames nothing
                # relevant changed.
                current_preview_line_count = (
                    len(selected_item.preview_text)
                    if selected_item is not None and selected_item.preview_text is not None
                    else 0
                )
                if current_preview_line_count != loop_state.last_preview_line_count:
                    preview_box = boxes.get("preview")
                    if preview_box is not None:
                        _, preview_y, _, preview_h = preview_box
                        inner_top, inner_rows = preview_y + 1, preview_h - 2
                        if inner_rows > 0:
                            stdscr.redrawln(inner_top, inner_rows)
                loop_state.last_preview_line_count = current_preview_line_count

            stdscr.refresh()

            key = stdscr.getch()

            if key == -1:
                continue
            loop_state.dismissed = False

            if loop_state.pending_confirm is not None:
                should_dismiss, loop_state.pending_confirm = handle_pending_confirm(action_ctx, loop_state.pending_confirm, key, cfg)
                do_apply_reselect(loop_state, action_ctx, ordered)
                do_apply_toast(loop_state, action_ctx)
                if should_dismiss:
                    loop_state.dismissed = True
                    provider.dismiss_self()
                continue

            global_item = global_shortcut_item(cfg.global_shortcuts, key)
            if global_item is not None:
                should_dismiss, loop_state.pending_confirm = dispatch_action(action_ctx, ACTION_HANDLERS, global_item, cfg)
                do_apply_reselect(loop_state, action_ctx, ordered)
                do_apply_toast(loop_state, action_ctx)
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
                        if loop_state.active_module == deleted_name:
                            loop_state.active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
                    elif key == cfg.keybinds["confirm_no"]:
                        resize_mode.confirm_delete_no(resize)
                    continue
                if key == cfg.keybinds["confirm"] and loop_state.active_module is not None:
                    box = next((b for b in cfg.layout.boxes if b.name == loop_state.active_module), None)
                    if box is not None:
                        do_enter_box_editing(loop_state, resize, box)
                    continue
                elif key == cfg.keybinds["delete_box"] and loop_state.active_module is not None:
                    box = next((b for b in cfg.layout.boxes if b.name == loop_state.active_module), None)
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
                do_apply_reselect(loop_state, action_ctx, ordered)
                do_apply_toast(loop_state, action_ctx)
                # sessions.py's/sysmon.py's own "name"/NICE actions call
                # start_naming()/start_nice_edit() on themselves —
                # main.py notices right after dispatch and claims the
                # stack on their behalf.
                if sessions_mode.is_naming():
                    loop_state.mode_stack.append("sessions_naming")
                if sysmon_mode.is_editing_nice():
                    loop_state.mode_stack.append("sysmon_nice")
                if connectivity_mode.is_browsing():
                    loop_state.mode_stack.append("connectivity_browsing")
                if should_dismiss:
                    loop_state.dismissed = True
                    provider.dismiss_self()

            elif key in next_item_keys and ordered:
                if any_two_level_module_expanded():
                    # Level 2 exception to "Tab never wraps, it rolls
                    # into the next module" — Escape/picking an action
                    # are the only ways out (see same_row_neighbor).
                    next_item = same_row_neighbor(ordered, loop_state.selected_id, direction=1, wrap=True)
                else:
                    # Walks forward past module boundaries until an
                    # actual item is found — zero-item modules (launcher,
                    # preview, clock) are common, one lookup isn't enough.
                    next_item = next_item_across_modules(ordered, module_names, loop_state.active_module, loop_state.selected_id)
                if next_item is not None:
                    loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(next_item, loop_state.focus_id)
            elif key in prev_item_keys and ordered:
                if any_two_level_module_expanded():
                    prev_item = same_row_neighbor(ordered, loop_state.selected_id, direction=-1, wrap=True)
                else:
                    prev_item = prev_item_across_modules(ordered, module_names, loop_state.active_module, loop_state.selected_id)
                    if (
                        prev_item is not None
                        and loop_state.active_module != "sessions"
                        and module_of_item(prev_item) == "sessions"
                    ):
                        # Sessions exception: always slot 1, not the
                        # module's last item — otherwise Shift+Tab-ing in
                        # lands on slot 3, one Tab from rolling back out.
                        prev_item = first_item_in_module(ordered, "sessions")
                    elif (
                        prev_item is not None
                        and loop_state.active_module != "sidebar"
                        and module_of_item(prev_item) == "sidebar"
                        and "sidebar" in boxes
                    ):
                        # Sidebar exception: prev_item_across_modules()
                        # picked sidebar's own last item OUT OF `ordered`
                        # — this frame's already-computed list, built
                        # with whatever selection was active BEFORE this
                        # keypress, which usually wasn't sidebar's own.
                        # sidebar.py's nav_items() windows its own
                        # content around ctx.selected_id (see
                        # CLAUDE/NOTES/design-decisions.md
                        # #sidebar-variable-height-windowing) — with
                        # selection elsewhere, that window is anchored
                        # near the top, so the TRUE last workspace
                        # commonly isn't in `ordered` for sidebar AT ALL,
                        # and no amount of scanning it finds an item
                        # that was never there. Re-query sidebar's own
                        # nav_items() directly with LAST_ITEM_QUERY (see
                        # that constant's own docstring) instead of
                        # trusting what `ordered` happened to contain.
                        query_ctx = dataclasses.replace(ctx, selected_id=LAST_ITEM_QUERY)
                        sidebar_items = NAV_PROVIDERS["sidebar"](boxes["sidebar"], query_ctx, "sidebar")
                        if sidebar_items:
                            prev_item = sidebar_items[-1]
                if prev_item is not None:
                    loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(prev_item, loop_state.focus_id)
            elif key in module_next_keys:
                # same_row_neighbor first: a row with multiple items
                # (e.g. sessions.py's expanded LOAD/SAVE/DEL/NAME) steps
                # across them before Right jumps to the next module.
                # None for the common single-column case — a no-op there.
                neighbor = same_row_neighbor(ordered, loop_state.selected_id, direction=1, wrap=any_two_level_module_expanded())
                if neighbor is not None:
                    loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(neighbor, loop_state.focus_id)
                else:
                    next_name = next_module_name(module_names, loop_state.active_module)
                    if next_name is not None:
                        loop_state.active_module = next_name
                        first_item = first_item_in_module(ordered, loop_state.active_module)
                        if first_item is not None:
                            loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(first_item, loop_state.focus_id)
                        else:
                            # Landed on an empty module (bars, clock,
                            # launcher...) — clear selected_id too, or
                            # the old item stays highlighted in the
                            # module we just left while this one's
                            # border also claims active.
                            loop_state.selected_id = None
            elif key in module_prev_keys:
                neighbor = same_row_neighbor(ordered, loop_state.selected_id, direction=-1, wrap=any_two_level_module_expanded())
                if neighbor is not None:
                    loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(neighbor, loop_state.focus_id)
                else:
                    prev_name = prev_module_name(module_names, loop_state.active_module)
                    if prev_name is not None:
                        loop_state.active_module = prev_name
                        first_item = first_item_in_module(ordered, loop_state.active_module)
                        if first_item is not None:
                            loop_state.selected_id, loop_state.active_module, loop_state.focus_id = resolve_selection(first_item, loop_state.focus_id)
                        else:
                            # See module_next_keys' matching branch above.
                            loop_state.selected_id = None
            elif key == cfg.keybinds["spawn_box"]:
                do_spawn_picker(loop_state, cfg, spawn_picker)
            elif key == cfg.keybinds["resize"] and loop_state.active_module is not None:
                do_enter_resize(resize)
            elif key == cfg.keybinds["save_layout"]:
                do_save_layout(loop_state, cfg, resize)
            elif key == cfg.keybinds["cycle_preset"]:
                do_cycle_preset(loop_state, cfg, resize)
            elif key == cfg.keybinds["new_preset"]:
                do_new_preset(loop_state, cfg, resize)
            elif key == cfg.keybinds["cycle_theme_preset"]:
                do_cycle_theme_preset(loop_state, cfg, help_state, stdscr, app)
            elif key == cfg.keybinds["help"]:
                do_enter_help(loop_state, help_state)
            elif cfg.vim_mode and not resize.active and key == cfg.keybinds["insert"]:
                launcher_mode.enter_typing_mode(launcher, loop_state.selected_id, loop_state.active_module, loop_state.focus_id)
                _apply_launcher_routing_default(loop_state, launcher, app)
                loop_state.mode_stack.append("launcher")
                loop_state.active_module = "launcher"
            elif not cfg.vim_mode and not resize.active and 32 <= key <= 126:
                launcher_mode.enter_typing_mode(launcher, loop_state.selected_id, loop_state.active_module, loop_state.focus_id, chr(key))
                _apply_launcher_routing_default(loop_state, launcher, app)
                loop_state.mode_stack.append("launcher")
                loop_state.active_module = "launcher"
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
                        loop_state.selected_id = f"{mod_name}:{collapsed}:row"
                        loop_state.active_module = mod_name
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
