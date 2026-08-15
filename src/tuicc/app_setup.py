"""One-time construction of every long-lived dependency main()'s loop
needs — backends, D-Bus agents, the shared StatusWorker — split out so
this whole "build the world" step can be read independent of the render
loop and curses event handling that follows it.

Not curses-free despite taking no `stdscr` argument: build_app() calls
theme_setup.setup_theme(), which calls curses.start_color(), which
requires curses.initscr() to already have run. True by the time main()
calls this (curses.wrapper(main) in main.py's own __main__ block
guarantees it) — NOT true if build_app() is ever called standalone,
which is also why there's no test file here (see build_app()'s own
docstring).
"""

from dataclasses import dataclass

from tuicc.config import load_config, Config
from tuicc.theme_setup import setup_theme, assign_control_toggle_pairs
from tuicc.actions import ActionContext
from tuicc.providers.base import Provider
from tuicc.providers.registry import build_provider
from tuicc.connectivity.base import WifiAgent
from tuicc.connectivity.registry import build_wifi_backend, build_wifi_agent, build_bluetooth_backend
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
from tuicc import weather


@dataclass
class AppContext:
    """What main()'s loop still needs once build_app() returns — every
    other name build_app() constructs (backends, samplers, the domains
    list itself) is folded into one of these fields and has no life of
    its own past that function.

    A third "*Context" type alongside RenderContext (context.py,
    per-frame render state) and ActionContext (actions.py, a
    TARGET_KIND handler's .provider/.connectivity) — same naming
    family, different scope: this one is whole-app-lifetime, built
    once, not rebuilt or narrowed per frame/action like the other two.
    """
    cfg: Config
    theme_pairs: dict
    control_colors: dict
    provider: Provider
    wifi_agent: WifiAgent
    bluez_agent: BluezAgent | None
    pid_feed: procmon.PidFeed
    status_worker: StatusWorker
    cava_reader: CavaReader
    action_ctx: ActionContext


def build_app(preset_override: int | None = None) -> AppContext:
    """Not unit-tested: setup_theme() below hits curses.start_color(),
    which raises outside a real curses.initscr() session, and
    build_provider()/the backend constructors can hit real WM IPC or
    D-Bus — same untestable-by-design territory theme_setup.py's own
    curses-touching functions are already in (tests/test_theme.py only
    covers the curses-free theme.py). Covered by the full test suite
    staying green plus a live-start smoke check instead.

    preset_override passes straight through to load_config() — main()
    computes it (config.pick_preset_for_size() against the real
    terminal grid, only available once curses.wrapper() has handed it
    stdscr) before calling build_app(), since build_app() itself stays
    deliberately stdscr-free otherwise (see this module's own docstring).
    """
    cfg = load_config(preset_override=preset_override)
    theme_pairs = setup_theme(cfg.theme)
    # Second curses-pair range, for [[control.toggle]] colors — must
    # start past theme_pairs' own range or the two collide.
    control_colors = assign_control_toggle_pairs(cfg.control_toggles, len(theme_pairs) + 1)
    provider = build_provider(cfg.provider_name)
    provider.mark_self(cfg.self_app_id)

    wifi_backend = build_wifi_backend(cfg.wifi_backend_name)
    bluetooth_backend = build_bluetooth_backend(cfg.bluetooth_backend_name)
    # Unconditional: every WIFI_BACKENDS name has a matching WIFI_AGENTS
    # entry (registry.py, enforced by test_registry.py) — no None-guard
    # needed, unlike bluez_agent below.
    wifi_agent = build_wifi_agent(cfg.wifi_backend_name)
    # Only one bluetooth backend exists today — conditional for parity
    # with wifi's config-driven selection, not a real branch yet.
    bluez_agent = BluezAgent() if cfg.bluetooth_backend_name == "bluez" else None
    audio_backend = build_audio_backend(cfg.audio_backend_name)
    media_backend = MprisBackend()
    # PidFeed: the "windows" Domain's poll() runs off the main thread
    # and must never touch `provider` — this is how it learns the
    # window list instead (procmon.py). Constructed once, not per-poll,
    # so CPU%/swap-rate/throttle samplers see consecutive polls' state.
    pid_feed = procmon.PidFeed()
    # Captured by the "windows" Domain's poll lambda below via a plain
    # closure — moving this whole function doesn't change that; the
    # lambda binds to build_app()'s own local scope same as it bound to
    # main()'s before.
    proc_sampler = procmon.ProcMonSampler()
    sysinfo_sampler = sysinfo.SysInfoSampler()
    sensors_source = sensors.SensorsSource()
    # One shared worker/thread for every domain below.
    domains = [
        Domain(
            name="wifi",
            poll=wifi_backend.get_networks,
            actions={
                "connect": wifi_backend.connect,
                # WifiBackend.disconnect() takes no arg — only one
                # network connects at a time.
                "disconnect": lambda ssid: wifi_backend.disconnect(),
                "scan": lambda _arg: wifi_backend.scan(),
            },
        ),
        Domain(
            name="bluetooth",
            poll=bluetooth_backend.get_devices,
            actions={
                "connect": bluetooth_backend.connect,
                "disconnect": bluetooth_backend.disconnect,
                "discover": lambda _arg: bluetooth_backend.start_discovery(),
            },
        ),
        # Separate tiny Domains, not folded into wifi/bluetooth above:
        # scan()/start_discovery() are fire-and-forget, so is_pending()
        # alone would only flicker for one D-Bus call — these poll the
        # daemon's own real Scanning/Discovering property instead.
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
                # set_volume/set_mute take a (sink_id, value) tuple as
                # request_action's single arg — see status_worker.py.
                "set_default_sink": audio_backend.set_default_sink,
                "set_volume": lambda arg: audio_backend.set_volume(arg[0], arg[1]),
                "set_mute": lambda arg: audio_backend.set_mute(arg[0], arg[1]),
            },
            # Shorter than the 5s default: the default sink can change
            # outside tuicc's own control flow (WirePlumber auto-
            # switching) — polling is the only way to notice.
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
            poll_interval=1,  # same reasoning as "audio" above
        ),
        # Tried as a push domain (battery.watch()) — reverted. See
        # CLAUDE/NOTES/known-limitations.md#battery-push-unreliable.
        Domain(
            name="battery",
            poll=lambda: battery.aggregate(battery.get_packs(), battery.get_ac_online()),
            actions={},
            poll_interval=0.3,
        ),
        Domain(
            name="brightness",
            poll=brightness.get_percent,
            actions={"set": brightness.set_percent},
            poll_interval=0.3,  # matches battery; brightnessctl's own subprocess cost is cheap at this rate
        ),
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
            # journalctl/systemctl calls are real work, but not
            # time-sensitive enough to need a tighter poll_interval.
        ),
    ]
    # One Domain per toggle, not shared — one toggle's status_command
    # erroring shouldn't blank every other toggle. "advance" is the
    # only action: modules/control.py computes the next state name and
    # passes it as the arg, this domain doesn't track state itself.
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
    # Only registered when [weather] is actually configured — an
    # unconfigured feature shouldn't show up as a permanently-erroring
    # domain. poll_interval=900 (15min) is the first genuinely long
    # interval in this codebase (everything else is 0.3-5s) — real
    # weather doesn't change fast, and this is a free API worth being
    # polite to. Location itself resolves once and caches inside
    # weather.py, not on this cadence — see resolve_location()'s own
    # docstring.
    if cfg.weather_lat is not None or cfg.weather_geoclue or cfg.weather_ip_approx:
        domains.append(Domain(name="weather", poll=lambda: weather.get_conditions(cfg), poll_interval=900))
    # push_worker.py/combined_status.py exist, tested, unused — see
    # CLAUDE/NOTES/known-limitations.md#battery-push-unreliable.
    status_worker = StatusWorker(domains)
    status_worker.start()

    # Unconditional, unlike cava_reader below — an agent must register
    # before any connect attempt could trigger a callback. Registration
    # failures surface via get_error(), not raised.
    wifi_agent.start()
    if bluez_agent is not None:
        bluez_agent.start()

    # Not a Domain — cava is a continuous stream, not poll-and-snapshot
    # (cava.py). Started/stopped lazily below, only while something's
    # actually playing.
    cava_reader = CavaReader()

    action_ctx = ActionContext(provider=provider, status=status_worker)

    return AppContext(
        cfg=cfg,
        theme_pairs=theme_pairs,
        control_colors=control_colors,
        provider=provider,
        wifi_agent=wifi_agent,
        bluez_agent=bluez_agent,
        pid_feed=pid_feed,
        status_worker=status_worker,
        cava_reader=cava_reader,
        action_ctx=action_ctx,
    )
