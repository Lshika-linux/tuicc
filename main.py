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
from tuicc.connectivity.iwd_agent import IwdAgent
from tuicc.connectivity.bluez_agent import BluezAgent
from tuicc.audio.registry import build_audio_backend
from tuicc.media.mpris import MprisBackend
from tuicc.media.cava import CavaReader
from tuicc.status_worker import StatusWorker, Domain
from tuicc import control
from tuicc import battery
from tuicc import brightness
from tuicc import procmon
from tuicc import sysinfo
from tuicc import sensors
from tuicc import diagnostics
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
from tuicc.theme_setup import setup_theme, reassign_theme_pairs, assign_control_toggle_pairs
from tuicc import resize_mode, help_mode, pending_moves
from tuicc.modules import launcher as launcher_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import media as media_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sidebar as sidebar_mode


def _resolve_visible_pids(windows, selected_id, resolved_pid_cache, provider, visible_slots):
    """Fills in `pid` for any procmon.WindowInfo currently missing one
    (i3 has no native pid on its own IPC tree — see providers/base.py's
    resolve_pid() docstring) by calling Provider.resolve_pid() here,
    main-thread, and ONLY for windows within sysmon.py's own currently-
    visible scroll window (sysmon_mode.visible_window_ids, `visible_slots`
    = cfg.sysmon_visible_slots — a real config.toml value, not a fixed
    constant, see [sysmon]'s own visible_slots key) — resolve_pid() is a
    real, possibly-slow on-demand X11 lookup on i3, so doing it for
    every window on every frame regardless of visibility would be
    wasted work for a long, mostly-scrolled-out-of-view window list.
    Resolved pids are cached indefinitely per window_id in
    `resolved_pid_cache` (main.py's own dict, not on the WindowInfo
    objects themselves, which are rebuilt fresh every frame from
    `state`) — a window that closes just stops appearing in `windows`
    on a later frame; its now-orphaned cache entry is harmless and
    small enough not to bother clearing, same accepted-small-growth
    tradeoff Provider.no_focus_next_window's own for_window rules
    already document (see CLAUDE.md).
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


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    # Second, independent round of curses pairs for [[control.toggle]]
    # states with an explicit `color` — must start one past whatever
    # theme_pairs already claimed so the two ranges never collide. See
    # assign_control_toggle_pairs' own docstring.
    control_colors = assign_control_toggle_pairs(cfg.control_toggles, len(theme_pairs) + 1)
    provider = build_provider(cfg.provider_name)
    provider.mark_self(cfg.self_app_id)

    wifi_backend = build_wifi_backend(cfg.wifi_backend_name)
    bluetooth_backend = build_bluetooth_backend(cfg.bluetooth_backend_name)
    # VISION.md's R4 — an agent only makes sense paired 1:1 with its
    # own protocol's backend, so it's gated on the same config key
    # rather than constructed unconditionally (also correctly keeps a
    # future NetworkManager backend from wrongly getting an iwd agent
    # registered alongside it). See iwd_agent.py/bluez_agent.py's own
    # module docstrings for why each needs its own dedicated thread
    # rather than being a StatusWorker Domain.
    iwd_agent = IwdAgent() if cfg.wifi_backend_name == "iwd" else None
    bluez_agent = BluezAgent() if cfg.bluetooth_backend_name == "bluez" else None
    audio_backend = build_audio_backend(cfg.audio_backend_name)
    media_backend = MprisBackend()
    # VISION.md's R6 (system monitor) — see procmon.py's own module
    # docstring for why PidFeed exists at all: the "windows" Domain's
    # poll() (below) runs on StatusWorker's own background thread and
    # must never touch `provider` directly, so PidFeed is how it learns
    # the current frame's window list instead. One instance of each,
    # constructed here (not per-poll) so the stateful samplers actually
    # see consecutive polls' prior state (CPU%/swap-rate/throttle are
    # all deltas — see procmon.py/sysinfo.py's own docstrings).
    pid_feed = procmon.PidFeed()
    proc_sampler = procmon.ProcMonSampler()
    sysinfo_sampler = sysinfo.SysInfoSampler()
    sensors_source = sensors.SensorsSource()
    # One shared worker/thread for every domain — wifi/bluetooth today,
    # audio/brightness/control-toggle domains (VISION.md's R5) register
    # against this exact same instance as they land, not a separate
    # worker each. See RenderContext.status's own docstring for why the
    # ctx field this feeds is named `status`, not `connectivity`.
    domains = [
        Domain(
            name="wifi",
            poll=wifi_backend.get_networks,
            actions={
                "connect": wifi_backend.connect,
                # WifiBackend.disconnect() takes no argument (only one
                # network is ever connected at a time) — the ssid arg
                # request_action always threads through is unused here,
                # same as ConnectivityWorker's old wifi_disconnect
                # dispatch discarded it too.
                "disconnect": lambda ssid: wifi_backend.disconnect(),
                # VISION.md's R4 — modules/connectivity.py's dedicated
                # "Scan" NavItem/handler. arg is always None (see
                # handle_wifi_scan), unused here same as disconnect above.
                "scan": lambda _arg: wifi_backend.scan(),
            },
        ),
        Domain(
            name="bluetooth",
            poll=bluetooth_backend.get_devices,
            actions={
                "connect": bluetooth_backend.connect,
                "disconnect": bluetooth_backend.disconnect,
                # VISION.md's R4 — modules/connectivity.py's dedicated
                # "Discover" NavItem/handler.
                "discover": lambda _arg: bluetooth_backend.start_discovery(),
            },
        ),
        # Their own tiny read-only Domains, not folded into "wifi"/
        # "bluetooth" above — found live, reported directly: the
        # "Scanning…"/"Discovering…" label only ever flickered, since
        # scan()/start_discovery() are fire-and-forget and
        # StatusWorker.is_pending() only reflects the brief window that
        # ONE D-Bus call takes, nowhere near the real scan/discovery
        # duration. is_scanning()/is_discovering() poll the daemon's
        # own real Scanning/Discovering property instead — see their
        # own docstrings in iwd.py/bluez.py. Short poll_interval (not
        # the 5s shared default) so the label catches up promptly —
        # same reasoning audio/media/battery/brightness's own
        # poll_interval overrides give.
        Domain(
            name="wifi_scanning",
            poll=wifi_backend.is_scanning,
            actions={},
            poll_interval=0.5,
        ),
        Domain(
            name="bluetooth_discovering",
            poll=bluetooth_backend.is_discovering,
            actions={},
            poll_interval=0.5,
        ),
        Domain(
            name="audio",
            poll=audio_backend.get_sinks,
            actions={
                # set_default_sink: modules/media.py's output switching
                # (VISION.md's R5: "route it to headphones").
                # set_volume/set_mute: modules/bars.py's VOL slider — both
                # take a (sink_id, value) tuple as request_action's single
                # `arg`, unpacked here (see status_worker.py's own
                # request_action docstring for why: this domain's actions
                # need two pieces of data, not the single id wifi/
                # bluetooth's own actions get away with).
                "set_default_sink": audio_backend.set_default_sink,
                "set_volume": lambda arg: audio_backend.set_volume(arg[0], arg[1]),
                "set_mute": lambda arg: audio_backend.set_mute(arg[0], arg[1]),
            },
            # Shorter than the 5s shared default (see Domain.poll_interval's
            # own docstring) — found live: the default sink can change
            # out from under tuicc entirely on its own (WirePlumber
            # auto-switches to a newly connected bluetooth device,
            # tuicc never touches request_action for that), so this is
            # the one domain where the ONLY way to notice a real change
            # promptly is polling itself, not an action-triggered re-poll.
            poll_interval=1,
        ),
        Domain(
            name="media",
            poll=media_backend.get_players,
            actions={
                "play_pause": media_backend.play_pause,
                "next": media_backend.next,
                "previous": media_backend.previous,
            },
            # Same reasoning as "audio" above — a track changing
            # mid-playback is also entirely outside tuicc's own control
            # flow, felt "stuck" at the 5s shared default when tested
            # live.
            poll_interval=1,
        ),
        # Tried as a push domain (battery.watch(), select.poll() on
        # /sys/class/power_supply/*/uevent) — reverted, see this
        # session's own live finding below. Back to a plain fast poll,
        # same mechanism proven to work for VOL/BRI.
        Domain(
            name="battery",
            poll=lambda: battery.aggregate(battery.get_packs(), battery.get_ac_online()),
            actions={},
            # Found live, empirically, on this exact machine (T480):
            # select.poll() on BAT0/BAT1's own `uevent` attribute never
            # fired once across several real charger unplug/replug
            # cycles — confirmed by running battery.watch() with a
            # 30s fallback and a live logger; every event that arrived
            # was exactly 30s apart (the fallback), never sooner, and
            # the underlying data hadn't changed anyway. The kernel's
            # documented power_supply sysfs poll() support evidently
            # isn't reliably wired up for this attribute on this kernel/
            # driver combination — not something user space can verify
            # ahead of time, and not something worth re-attempting
            # blindly. battery.watch() itself is left in place (tested,
            # does what its own docstring says) for a future revisit on
            # different hardware, just not wired in here anymore.
            poll_interval=0.3,
        ),
        Domain(
            name="brightness",
            poll=brightness.get_percent,
            actions={"set": brightness.set_percent},
            # Same reasoning as "battery" above — matched to the same
            # 300ms so neither one is the bottleneck under the other.
            # Real (if small) subprocess-spawn cost per poll here
            # (brightnessctl -m), unlike battery's free /sys reads —
            # ~3/s is still cheap on any real machine, revisit only if
            # that stops being true somewhere.
            poll_interval=0.3,
        ),
        # VISION.md's R6 — see procmon.py's own module docstring for why
        # this reads pids via PidFeed rather than closing over `provider`
        # directly (Provider stays main-thread-only, same as every other
        # Domain here).
        Domain(
            name="windows",
            poll=lambda: proc_sampler.poll(pid_feed.get()),
            actions={},
        ),
        Domain(
            name="sysinfo",
            poll=sysinfo_sampler.poll,
            actions={},
        ),
        Domain(
            name="sensors",
            poll=sensors_source.poll,
            actions={},
        ),
        Domain(
            name="diagnostics",
            poll=diagnostics.poll_diagnostics,
            actions={},
            # journalctl/systemctl subprocess calls are real (if small)
            # work — same "don't hammer it" reasoning as brightness's
            # own poll_interval, just far less time-sensitive (failed
            # units/OOM/log errors don't need sub-second freshness the
            # way VOL/BRI/BAT do), so this stays on StatusWorker's
            # shared default interval rather than tightening it.
        ),
    ]
    # One Domain per [[control.toggle]] entry, not one shared domain
    # for all of them — same granular-error-surfacing reasoning as the
    # wifi/bluetooth split: one toggle's status_command erroring (a
    # missing binary, say) shouldn't blank out every other toggle's
    # state too. poll() returns the current state's name (a plain str,
    # not a list — StatusWorker.get()/snapshots are generic, no
    # per-domain shape requirement). "advance" is the only action —
    # modules/control.py computes which state name comes next (Enter
    # always advances, wrapping) and passes it as request_action's arg;
    # this domain doesn't need to know current state itself, just how
    # to enter whichever one it's told to.
    for i, toggle in enumerate(cfg.control_toggles):
        domains.append(Domain(
            name=f"toggle:{i}",
            poll=lambda toggle=toggle: control.find_current_state(toggle["states"], toggle["shell_true"]),
            actions={
                "advance": lambda target_name, toggle=toggle: control.run_state_command(
                    toggle["states"], target_name, toggle["shell_true"]
                ),
            },
        ))
    # push_worker.py/combined_status.py (PushWorker/CombinedStatus) exist
    # in this codebase, tested, but aren't wired in here — "battery" was
    # their pilot domain and got reverted (see its own Domain above for
    # why). Left in place for a future domain with a genuinely reliable
    # event source (a D-Bus signal, or a subprocess --subscribe stream —
    # unlike sysfs poll(), which is what this pilot found NOT to hold
    # here), not wired up "just in case" in the meantime.
    status_worker = StatusWorker(domains)
    status_worker.start()

    # Unconditional, unlike cava_reader below (lazy) — an agent must be
    # registered before any connect attempt can trigger a callback,
    # there's no "only start once something's happening" analog for a
    # D-Bus agent the way cava has "only run while something's
    # playing". A registration failure is caught inside start() itself
    # (see its own docstring) rather than raised — surfaced via
    # get_error(), folded into wifi_error/bluetooth_error below rather
    # than crashing startup.
    if iwd_agent is not None:
        iwd_agent.start()
    if bluez_agent is not None:
        bluez_agent.start()

    # NOT a Domain — cava is a continuous stream, not a periodic
    # poll-and-snapshot, so it isn't wired through StatusWorker at all
    # (see cava.py's own module docstring). Constructed here but not
    # started yet: main.py's own loop below starts/stops it lazily
    # based on whether anything is actually playing, not unconditionally
    # for tuicc's whole lifetime — a continuous audio capture + FFT
    # process running even while nothing plays (or tuicc is dismissed)
    # would be work with no payoff, not a meaningful resource problem in
    # isolation (cava itself is lightweight) but free to avoid regardless.
    cava_reader = CavaReader()  # bars defaults to 8 — see cava.py's own docstring

    action_ctx = ActionContext(provider=provider, status=status_worker)

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

    # VISION.md's R2 (input claim): which module currently owns raw
    # keystrokes, generalizing what used to be launcher.typing_mode
    # checked directly. None means no one has stolen input — an
    # unbound printable key auto-claims for the launcher (ambient
    # "type anywhere" is sugar for that auto-claim), same as before.
    # Three consumers so far: "launcher" (typing/search), "help_colors"
    # (help_mode's inline color editor), "sessions_naming" (sessions.py's
    # rename field) — all genuine text-input claims, the shape R2 was
    # designed for. resize_mode's own two-level browsing/editing session
    # (resize.active/resize.editing) is deliberately NOT migrated onto
    # this — it isn't a text-input claim at all (arrow-key resize
    # deltas, move_toggle, delete_box, F1/F3/F4/F6 reachable from either
    # level), a structurally different kind of hijack; see VISION.md's
    # R2 section for why that'd be a bigger, separate pass. Each
    # consumer's own module-level state (LauncherState.typing_mode,
    # HelpState.color_editing, sessions.py's _naming_slot) is untouched
    # by this — this variable is kept in sync with it at every entry/
    # exit point below, not a replacement for it.
    input_claim = None

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
    # window_id -> pid, filled in lazily by _resolve_visible_pids() below
    # (i3 only — sway's Window.pid is already populated from get_state()
    # directly, see model.py's own docstring) — main.py-owned, not
    # sysmon.py's, since resolving a pid needs `provider`, which no
    # module's draw()/nav_items() ever touches (see CLAUDE.md).
    resolved_pid_cache = {}

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

    def any_two_level_module_expanded():
        # sessions.py and media.py each own a two-level browsing/
        # expanded session (see media.py's own module docstring for
        # why it needed the same model sessions.py established) — Tab/
        # Shift+Tab/Left/Right's wrap behavior needs to know if EITHER
        # is currently expanded, not just sessions specifically. A
        # small helper here instead of repeating "sessions_mode.
        # is_expanded() or media_mode.is_expanded()" at every call
        # site — only one of the two can plausibly be expanded at once
        # in practice (each is scoped to its own module, and leaving a
        # module auto-collapses it, see the per-frame check above), but
        # the OR is what actually stays correct if that ever changes.
        # sysmon.py's window rows (VISION.md's R6) are a third module
        # using this same two-level model, added the same way.
        return sessions_mode.is_expanded() or media_mode.is_expanded() or sysmon_mode.is_expanded()

    # No `break` anywhere below this point — tuicc's lifecycle model
    # (VISION.md section 2) is a persistent process the WM shows/hides;
    # every former "exit" site now calls provider.dismiss_self() and
    # keeps looping. The only way out is an unhandled exception, in
    # practice Ctrl+C (caught at the bottom of this file) — this
    # try/finally is what makes that a clean shutdown rather than a
    # daemon thread just getting killed mid-poll.
    try:
        while True:
            # Third tier between the two below: media.py's now-playing
            # marquee scroll advances one character every
            # MARQUEE_STEP_SECONDS of wall-clock time regardless — but
            # the idle 1000ms redraw cadence below meant it only ever
            # got REDRAWN once a second, turning a smooth 1-character
            # slide into a visible ~3-character jump. Found live.
            marquee_active = media_mode.has_scrolling_content(status_worker.get("media"))

            # Lazy cava lifecycle: only run the visualizer subprocess
            # while it could actually show something AND the current
            # preset even has a media box to show it in — a preset
            # without "media" in its layout still has the media Domain
            # polling (every Domain is unconditional, see main.py's own
            # domain construction above), so gating on media_module_present
            # too avoids spinning up an audio-capture process for a
            # visualizer nothing on screen would ever render.
            media_module_present = any(b.name == "media" for b in cfg.layout.boxes)
            media_players = status_worker.get("media") or []
            any_playing = any(p.playback_status == "Playing" for p in media_players)
            if media_module_present and any_playing:
                cava_reader.start()
            else:
                cava_reader.stop()

            # VISION.md's R4 — a live iwd/bluez agent callback claims
            # input at the highest priority of anything in this file
            # (ahead of sessions_naming/sysmon_nice/help_colors below):
            # unlike an in-progress rename, a pending passphrase/pairing
            # prompt can be silently cancelled by the daemon itself at
            # any moment (network went away, the daemon's own timeout
            # fired — see agent_mailbox.py's module docstring), so it
            # deserves more urgency than risking it expire unanswered
            # while the user finishes an unrelated edit. Gated the same
            # way every other hijack tier is implicitly gated (only
            # reachable when nothing else already owns input) so it
            # can't steal a keystroke out from under an already-open
            # session.
            # Resolves a "waiting for the real connect/pair result"
            # overlay (see modules/connectivity.py's mark_passphrase_
            # submitted()/mark_pairing_submitted() docstrings) — checked
            # every frame, not just on keypress, so a result that
            # arrives while the user isn't pressing anything is still
            # noticed promptly (StatusWorker.has_pending() being true
            # for the whole wait already keeps the render loop on its
            # fast 50ms tick, see stdscr.timeout()'s own condition
            # below). Found live, reported directly ("gaslightuje tě že
            # se ani nic nedělo"): the original version closed this
            # overlay the instant Enter was pressed, before iwd had
            # even tried the (possibly wrong) passphrase.
            if connectivity_mode.is_entering_passphrase() and connectivity_mode.is_passphrase_waiting():
                ssid = connectivity_mode.entering_passphrase_ssid()
                if not status_worker.is_pending("wifi", ssid):
                    error = status_worker.get_action_error_for("wifi", ssid)
                    if error:
                        connectivity_mode.set_passphrase_error(error)
                    else:
                        connectivity_mode.cancel_passphrase_entry()
                        input_claim = None
            if connectivity_mode.is_confirming_pairing() and connectivity_mode.is_pairing_waiting():
                pairing_request = connectivity_mode.current_pairing_request()
                if not status_worker.is_pending("bluetooth", pairing_request.device_id):
                    error = status_worker.get_action_error_for("bluetooth", pairing_request.device_id)
                    if error:
                        connectivity_mode.set_pairing_error(error)
                    else:
                        connectivity_mode.cancel_pairing_confirm()
                        input_claim = None

            connectivity_wants_input = (
                input_claim is None and pending_confirm is None
                and not resize.active and not spawn_picker.active and not help_state.active
            )
            # Also fires while ALREADY in one of these two tiers, not
            # just when claiming input fresh — a wrong passphrase makes
            # iwd call RequestPassphrase a SECOND time (a retry), which
            # publishes a brand new AgentMailbox request while
            # input_claim is still "connectivity_passphrase" from the
            # first attempt; without this, that second request would
            # never be noticed (the "claim fresh" branch only runs
            # while input_claim is None). See modules/connectivity.py's
            # start_passphrase_entry()'s own docstring.
            #
            # ONLY once we're actually waiting/showing an error
            # (mark_passphrase_submitted()/mark_pairing_submitted()
            # already called) — NOT while the user is still typing.
            # AgentMailbox.get_request() (used above and below) never
            # pops the request, so has_pending() stays True for the
            # WHOLE time the original prompt sits there unanswered —
            # found live, reported directly ("prompt nechce brát
            # input"): gating on input_claim alone made this fire every
            # single frame while typing, since the mailbox's own
            # pending request never changes until it's actually
            # submitted — each frame's re-fire called
            # start_passphrase_entry() again, silently wiping whatever
            # had just been typed back to empty before the next
            # keypress could even land.
            if iwd_agent is not None and iwd_agent.mailbox.has_pending() and (
                connectivity_wants_input
                or (input_claim == "connectivity_passphrase" and connectivity_mode.is_passphrase_waiting())
            ):
                request = iwd_agent.mailbox.get_request()
                connectivity_mode.start_passphrase_entry(request.ssid)
                input_claim = "connectivity_passphrase"
            elif bluez_agent is not None and bluez_agent.mailbox.has_pending() and (
                connectivity_wants_input
                or (input_claim == "connectivity_pairing" and connectivity_mode.is_pairing_waiting())
            ):
                request = bluez_agent.mailbox.get_request()
                connectivity_mode.start_pairing_confirm(request)
                input_claim = "connectivity_pairing"

            agent_has_pending = (
                (iwd_agent is not None and iwd_agent.mailbox.has_pending())
                or (bluez_agent is not None and bluez_agent.mailbox.has_pending())
            )
            stdscr.timeout(
                50 if (moves.entries or action_ctx.restore_queue or status_worker.has_pending()
                       or resize_message is not None or agent_has_pending)
                else int(media_mode.CAVA_REDRAW_SECONDS * 1000) if cava_reader.is_running()
                else int(media_mode.MARQUEE_STEP_SECONDS * 1000) if marquee_active
                # 300, not 1000 — found live, reported by the user: even
                # with a Domain's own poll_interval tightened (battery/
                # brightness/audio/media all poll at 1s now), the render
                # loop itself only ever LOOKS at a fresh StatusWorker
                # snapshot once per idle getch() timeout — a 1s idle
                # redraw cadence stacks up to ~1s of its own lag on top
                # of whatever the poll cadence already contributes.
                # Cheap to lower: nothing here is fetched from a remote
                # source or does real work, every module's draw() already
                # reruns unconditionally every frame regardless of
                # cadence (see CLAUDE.md's "nothing is cached per-frame"),
                # so redrawing 3.3x more often while idle just means
                # doing the same cheap recompute more often, not new work.
                else 300
            )
            stdscr.erase()

            term_height, term_width = stdscr.getmaxyx()
            boxes = compute_boxes(cfg.layout, term_width, term_height)
            state = provider.get_state()

            # VISION.md's R6 — publish this frame's window list for the
            # background "windows" Domain to pick up next poll (see
            # procmon.py's own module docstring for why this crosses the
            # thread boundary via PidFeed rather than a Domain reading
            # `provider`/`state` directly). Resolving i3's missing pids
            # here, main-thread, right after `state` itself is fresh, is
            # deliberately BEFORE this frame's own RenderContext/nav_items
            # are built below — sysmon_mode.visible_window_ids() only
            # needs `selected_id` (already current from last frame) and
            # this frame's own flattened window list, not anything
            # RenderContext provides. Sorted the same "most resource-
            # intensive first" way sysmon.py's own _build_rows()/
            # nav_items() sort for display (against the most recently
            # KNOWN sample, since this frame's own WindowInfo objects
            # have no cpu/rss of their own yet) — otherwise the lazy
            # pid-resolution below would decide "visible" using a
            # different order than what's actually about to be shown.
            windows_this_frame = sysmon_mode.sort_windows_by_drain(
                procmon.flatten_windows(state), known_stats=status_worker.get("windows") or [],
            )
            pid_feed.set(_resolve_visible_pids(
                windows_this_frame, selected_id, resolved_pid_cache, provider, cfg.sysmon_visible_slots,
            ))

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
            # Same idea, media.py's own now-playing row expand/collapse
            # — see media.py's module docstring for why it needed this
            # same two-level model sessions.py established.
            if active_module != "media" and media_mode.is_expanded():
                media_mode.collapse()
            # Same idea, sysmon.py's own window-row expand/collapse
            # (VISION.md's R6) — see media.py's module docstring for
            # why this two-level model needed to be reused a third time.
            if active_module != "sysmon" and sysmon_mode.is_expanded():
                sysmon_mode.collapse()

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
                wifi_networks=status_worker.get("wifi"),
                bluetooth_devices=status_worker.get("bluetooth"),
                # A poll failure (backend unreachable at all) takes
                # priority over an agent registration failure (backend
                # fine, but unknown-network/unpaired-device connects
                # silently keep failing) — the former is the more
                # complete outage. See IwdAgent/BluezAgent.start()'s
                # own docstrings for why a registration failure is
                # caught rather than raised.
                wifi_error=status_worker.get_error("wifi") or (iwd_agent.get_error() if iwd_agent else None),
                bluetooth_error=status_worker.get_error("bluetooth") or (bluez_agent.get_error() if bluez_agent else None),
                status=status_worker,
                session_preview=sessions_mode.expanded_preview(),
                control_colors=control_colors,
                cava=cava_reader,
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

            if input_claim == "connectivity_passphrase":
                # First (highest-priority) of R4's two new input_claim
                # consumers — see the "connectivity_wants_input" block
                # above for why these sit ahead of sessions_naming/
                # sysmon_nice/help_colors.
                if connectivity_mode.is_passphrase_waiting():
                    # Resolution happens in the top-of-loop check above
                    # (every frame, not keypress-driven) — this just
                    # swallows stray keys while the real connect
                    # attempt is still in flight.
                    continue
                if connectivity_mode.passphrase_error() is not None:
                    # A resolved failure, already shown — any key
                    # dismisses back to normal navigation. A genuine
                    # retry means Enter on the network row again (a
                    # fresh connect() attempt, possibly a fresh
                    # RequestPassphrase too) — the original request
                    # this overlay was answering is long since resolved
                    # one way or another, there's nothing left to
                    # resubmit here.
                    connectivity_mode.cancel_passphrase_entry()
                    input_claim = None
                    continue
                # The daemon can end this on its own at any time
                # (Cancel/Release — see agent_mailbox.py's module
                # docstring); has_pending() going False here (NOT
                # while waiting/showing an error, both handled above)
                # is exactly that, not a bug.
                if iwd_agent is None or not iwd_agent.mailbox.has_pending():
                    connectivity_mode.cancel_passphrase_entry()
                    input_claim = None
                elif key == cfg.keybinds["confirm"]:
                    text = connectivity_mode.apply_passphrase()
                    if text is not None:
                        iwd_agent.reply_passphrase(text)
                        connectivity_mode.mark_passphrase_submitted()
                elif key == 27:  # Escape
                    connectivity_mode.cancel_passphrase_entry()
                    iwd_agent.cancel_current()
                    input_claim = None
                elif not connectivity_mode.handle_passphrase_key(key):
                    connectivity_mode.cancel_passphrase_entry()
                    input_claim = None
                continue

            if input_claim == "connectivity_pairing":
                # Second of R4's two new input_claim consumers — plain
                # yes/no, not typed text, so it's resolved directly
                # here with confirm_yes/confirm_no rather than a
                # handle_*_key()/apply_*() pair, same convention
                # handle_pending_confirm() already uses. Only the
                # ACCEPT path waits for a real result — confirm_no/
                # Escape are the user's own explicit choice, nothing
                # further to report about those.
                if connectivity_mode.is_pairing_waiting():
                    continue
                if connectivity_mode.pairing_error() is not None:
                    connectivity_mode.cancel_pairing_confirm()
                    input_claim = None
                    continue
                if bluez_agent is None or not bluez_agent.mailbox.has_pending():
                    connectivity_mode.cancel_pairing_confirm()
                    input_claim = None
                elif key == cfg.keybinds["confirm_yes"]:
                    bluez_agent.reply_pairing(True)
                    connectivity_mode.mark_pairing_submitted()
                elif key == cfg.keybinds["confirm_no"]:
                    bluez_agent.reply_pairing(False)
                    connectivity_mode.cancel_pairing_confirm()
                    input_claim = None
                elif key == 27:  # Escape — same as an explicit reject
                    bluez_agent.cancel_current()
                    connectivity_mode.cancel_pairing_confirm()
                    input_claim = None
                continue

            if input_claim == "sessions_naming":
                # Second consumer of R2's input_claim (see its own
                # comment near where it's declared) — a narrow key-
                # capture hijack, not a full modal like help_state.active/
                # resize.active. The early `continue` here is what keeps
                # Tab/other keys from leaking through to the normal
                # dispatch chain mid-rename (handle_naming_key itself
                # just ignores anything outside backspace/Escape/
                # printable-ASCII, so a stray Tab is silently a no-op,
                # not a navigation change).
                if key == cfg.keybinds["confirm"]:
                    result = sessions_mode.apply_naming()
                    if result is not None:
                        slot, new_name = result
                        cfg.session_names[slot] = new_name or f"Slot {slot}"
                        set_session_name(slot, new_name)
                        input_claim = None
                elif not sessions_mode.handle_naming_key(key):
                    input_claim = None
                continue

            if input_claim == "sysmon_nice":
                # Fourth consumer of R2's input_claim — sysmon.py's own
                # NICE value entry, same narrow key-capture shape as
                # sessions_naming right above (a digits-only field, not
                # a full modal). apply_nice_edit() itself calls
                # os.setpriority() — there's no cfg/theme state to
                # thread through here the way sessions_naming's rename
                # needs cfg.session_names/set_session_name, so this
                # tier is even simpler than that one.
                if key == cfg.keybinds["confirm"]:
                    result = sysmon_mode.apply_nice_edit()
                    if result is not None:
                        input_claim = None
                elif not sysmon_mode.handle_nice_key(key):
                    input_claim = None
                continue

            if input_claim == "help_colors":
                # Third consumer — extracted out of help_state.active's
                # own dispatch below into its own early tier for the
                # same reason sessions_naming is: it's a text-input
                # claim (type a color, Enter applies, Escape cancels),
                # not part of the page-selection/menu navigation
                # help_state.active otherwise handles. input_claim only
                # clears on a SUCCESSFUL apply — an invalid color value
                # (apply_color_edit returns None, state.color_error set
                # for display) keeps editing open so the user can just
                # fix it and retry, same as before this migration.
                if key == cfg.keybinds["confirm"]:
                    result = help_mode.apply_color_edit(help_state)
                    if result is not None:
                        role, color, typed_value = result
                        cfg.theme[role] = color
                        theme_pairs = reassign_theme_pairs(cfg.theme)
                        set_theme_color(role, typed_value)
                        input_claim = None
                elif not help_mode.type_color_key(help_state, key):
                    input_claim = None
                continue

            if help_state.active:
                if help_state.page is None:
                    help_mode.select_page(help_state, key)
                    if help_state.page is None and key == 27:  # Escape
                        help_state.active = False
                    continue

                if help_state.page == "colors":
                    if key == cfg.keybinds["up"]:
                        help_mode.move_color_index(help_state, -1)
                    elif key == cfg.keybinds["down"]:
                        help_mode.move_color_index(help_state, 1)
                    elif key == cfg.keybinds["confirm"]:
                        help_mode.start_color_edit(help_state, get_raw_theme_values())
                        input_claim = "help_colors"
                    elif key == 27:  # Escape
                        help_state.page = None
                    continue

                if key == 27:  # Escape
                    help_state.page = None
                continue

            if input_claim == "launcher":
                # Up/Down shift the ambient-typing launch target
                # (focus_id) without leaving typing mode — found live,
                # asked for directly: launching from anywhere always
                # targeted focus_id regardless of which module you'd
                # been browsing, with no way to see (let alone change)
                # that target while typing. Left/Right are already
                # spoken for by launcher_mode.handle_typing_key itself
                # (they move which search RESULT is selected); arrow
                # keys never collide with typed characters (outside the
                # printable range that function checks), including
                # under vim_mode — vim's own j/k duplicates are a
                # separate keybind (cfg.keybinds["vim_down"/"vim_up"]),
                # not checked here, so they still type normally.
                if key == cfg.keybinds["up"]:
                    current = focus_id if focus_id is not None else state.focused_region_id
                    focus_id = sidebar_mode.shift_workspace_id(current, cfg.total_workspaces, -1)
                elif key == cfg.keybinds["down"]:
                    current = focus_id if focus_id is not None else state.focused_region_id
                    focus_id = sidebar_mode.shift_workspace_id(current, cfg.total_workspaces, 1)
                elif key == cfg.keybinds["confirm"]:
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
                        input_claim = None
                        selected_id = launcher.saved_selected_id
                        active_module = launcher.saved_active_module
                    # selected is None (no search results): nothing happens,
                    # typing_mode stays True — not an implicit cancel.
                elif not launcher_mode.handle_typing_key(launcher, key, cfg):
                    # handle_typing_key exited on its own (Escape, or
                    # Backspace on an empty query) — release the claim
                    # the same way the confirm branch above does
                    # explicitly, and restore the pre-typing selection
                    # the same way a successful confirm does too.
                    input_claim = None
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
                # sessions.py's "name" action calls start_naming() on
                # itself (module-owned state, same as _expanded_slot) —
                # this is where main.py notices and claims input on its
                # behalf, mirroring how help_state's own "confirm" branch
                # sets input_claim = "help_colors" right next to its own
                # start_color_edit() call above.
                if sessions_mode.is_naming():
                    input_claim = "sessions_naming"
                # sysmon.py's own NICE action calls start_nice_edit() on
                # itself (module-owned state, same as sessions.py's
                # naming field) — same "main.py notices right after
                # dispatch, claims input on the module's behalf" idiom
                # as sessions_naming/help_colors above.
                if sysmon_mode.is_editing_nice():
                    input_claim = "sysmon_nice"
                if should_dismiss:
                    dismissed = True
                    provider.dismiss_self()

            elif key in next_item_keys and ordered:
                if any_two_level_module_expanded():
                    # Level 2 is a deliberate exception to the app-wide
                    # "Tab never wraps within a module, it rolls into
                    # the next one" rule — see same_row_neighbor's
                    # wrap param docstring. Escape/picking an action
                    # are the only ways out (see the dedicated Escape
                    # branch and the per-frame auto-collapse check
                    # above), so Tab/Shift+Tab/Left/Right all just
                    # cycle LOAD/SAVE/DEL/NAME (or media.py's
                    # </PAUSE/PLAY/>>) in place instead.
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
                if any_two_level_module_expanded():
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
                input_claim = "launcher"
                active_module = "launcher"
            elif not cfg.vim_mode and not resize.active and 32 <= key <= 126:
                launcher_mode.enter_typing_mode(launcher, selected_id, active_module, chr(key))
                input_claim = "launcher"
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
            elif key == 27 and media_mode.is_expanded():
                # Same idea as sessions.py's own Escape-collapse branch
                # above, mirrored for media.py's now-playing rows — see
                # media.py's module docstring for why it needed the
                # same two-level model.
                collapsed_bus_name = media_mode.collapse()
                if collapsed_bus_name is not None:
                    selected_id = f"media:{collapsed_bus_name}:row"
                    active_module = "media"
            elif key == 27 and sysmon_mode.is_expanded():
                # Same idea as sessions.py's/media.py's own Escape-
                # collapse branches above, mirrored for sysmon.py's
                # window rows (VISION.md's R6).
                collapsed_window_id = sysmon_mode.collapse()
                if collapsed_window_id is not None:
                    selected_id = f"sysmon:{collapsed_window_id}:row"
                    active_module = "sysmon"
            elif key == 27:  # Escape, no active input claim: dismiss at top level
                if cfg.return_to_origin and origin_region_id is not None:
                    provider.focus_region(origin_region_id)
                dismissed = True
                provider.dismiss_self()
    finally:
        status_worker.stop()
        cava_reader.stop()
        if iwd_agent is not None:
            iwd_agent.stop()
        if bluez_agent is not None:
            bluez_agent.stop()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
