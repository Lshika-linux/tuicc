"""Smoke tests for every module's draw() — plus help_mode/resize_mode's
own directly-drawn overlays and render_utils.draw_status_line() — the
untested half this codebase has, until now, accepted as untestable.

That premise turns out to be false. Every module's draw() (confirmed
by grepping all 13 entries in render.py's MODULES) only ever calls
stdscr.addstr(y, x, text, attr) — never .erase()/.getmaxyx()/.bkgd()/
curses.color_pair()/curses.init_pair(), all of which live one layer up
(render_utils.py/theme_setup.py/main.py), never inside a module's own
draw(). A fake stdscr that accepts addstr() and does nothing is a
complete, sufficient stand-in — no real curses.initscr() needed.

This is a crash/no-op safety net, not a rendering-correctness suite:
no pixel-exact content assertions, same "did this raise" bar
test_fresh_install_smoke.py already set for collect_nav_items(), one
layer further in. Found live, the exact bug this exists to catch: a
stale variable reference in preview.py's empty-workspace label crashed
tuicc outright — draw()'s untested status let it ship.
"""

from types import SimpleNamespace

from i3ipc import Con
import json
from pathlib import Path

from tuicc import config as config_module
from tuicc import render
from tuicc import render_utils
from tuicc import resize_mode
from tuicc import help_mode
from tuicc.layout_engine import compute_boxes
from tuicc.model import WMState
from tuicc.context import RenderContext
from tuicc.providers.sway import parse_tree
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules import media as media_mode
from tuicc.modules import control as control_mode

from _fresh_install_helpers import FreshInstallStatus, load_packaged_default_config

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeStdscr:
    """The entire contract every module's draw() actually needs (see
    this file's own docstring). Records every call so a test can also
    assert "this drew *something*", not just "this didn't crash" —
    catches an accidentally-empty draw() too.
    """
    def __init__(self):
        self.calls = []

    def addstr(self, y, x, text, attr=0):
        self.calls.append((y, x, text, attr))


def _sway_fixture_state():
    with open(FIXTURES / "sway_basic.json") as f:
        return parse_tree(Con(json.load(f), None, None))


def _ctx(cfg, state=None, focus_id=None, selected_id=None, active_module=None):
    return RenderContext(
        state=state if state is not None else WMState(),
        selected_id=selected_id,
        focus_id=focus_id,
        theme=cfg.theme,
        config=cfg,
        active_module=active_module,
        status=FreshInstallStatus(),
        preview_renderers=render.PREVIEW_RENDERERS,
    )


# ---------- broad pass: draw_all() across realistic states ----------

def _draw_all_does_not_crash(cfg, state, term_width, term_height, focus_id=None):
    boxes = compute_boxes(cfg.layout, term_width=term_width, term_height=term_height)
    ctx = _ctx(cfg, state=state, focus_id=focus_id, active_module="sidebar")
    stdscr = _FakeStdscr()

    render.draw_all(stdscr, cfg.layout, boxes, ctx)

    assert stdscr.calls  # drew *something* — not just a silent no-op


def test_draw_all_empty_state_large_terminal(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    _draw_all_does_not_crash(cfg, WMState(), term_width=200, term_height=55)


def test_draw_all_empty_state_small_terminal(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    _draw_all_does_not_crash(cfg, WMState(), term_width=80, term_height=24)


def test_draw_all_populated_state_large_terminal(tmp_path, monkeypatch):
    # Real regions/windows, including a floating one — this is what
    # actually reaches preview.py's tiled/floating branches, sidebar's
    # multi-window rows, sysmon's per-window list. WMState() alone
    # (the two tests above) never exercises any of that.
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    state = _sway_fixture_state()
    _draw_all_does_not_crash(cfg, state, term_width=200, term_height=55, focus_id=state.focused_region_id)


def test_draw_all_populated_state_small_terminal(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    state = _sway_fixture_state()
    _draw_all_does_not_crash(cfg, state, term_width=80, term_height=24, focus_id=state.focused_region_id)


# ---------- targeted pass: one test per non-default UI state ----------
# draw_all() above only ever reaches each module's default/browsing-
# level render. These hit the mode_stack tiers and two-level
# expansions that only show up mid-interaction — exactly the kind of
# state a generic sweep wouldn't reach on its own.

def test_connectivity_draw_while_browsing(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    connectivity_mode.start_browsing("wifi")
    try:
        ctx = _ctx(cfg, active_module="connectivity")
        stdscr = _FakeStdscr()
        connectivity_mode.draw(stdscr, (0, 0, 60, 20), ctx, "connectivity")
        assert stdscr.calls
    finally:
        connectivity_mode.stop_browsing()


def test_connectivity_draw_during_passphrase_entry(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    connectivity_mode.start_passphrase_entry("some-network")
    try:
        ctx = _ctx(cfg, active_module="connectivity")
        stdscr = _FakeStdscr()
        connectivity_mode.draw(stdscr, (0, 0, 60, 20), ctx, "connectivity")
        assert stdscr.calls
    finally:
        connectivity_mode.cancel_passphrase_entry()


def test_connectivity_draw_during_pairing_confirm(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    request = SimpleNamespace(kind="confirm", device_id="AA:BB:CC:DD:EE:FF", device_name="Some Headphones", passkey=847291)
    connectivity_mode.start_pairing_confirm(request)
    try:
        ctx = _ctx(cfg, active_module="connectivity")
        stdscr = _FakeStdscr()
        connectivity_mode.draw(stdscr, (0, 0, 60, 20), ctx, "connectivity")
        assert stdscr.calls
    finally:
        connectivity_mode.cancel_pairing_confirm()


def test_connectivity_draw_during_hidden_ssid_entry(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    connectivity_mode.start_hidden_ssid_entry()
    try:
        ctx = _ctx(cfg, active_module="connectivity")
        stdscr = _FakeStdscr()
        connectivity_mode.draw(stdscr, (0, 0, 60, 20), ctx, "connectivity")
        assert stdscr.calls
    finally:
        connectivity_mode.cancel_hidden_ssid_entry()


def test_connectivity_draw_during_forget_confirm(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    connectivity_mode.start_browsing("wifi")
    connectivity_mode.request_forget("some-network")
    try:
        ctx = _ctx(cfg, active_module="connectivity")
        stdscr = _FakeStdscr()
        connectivity_mode.draw(stdscr, (0, 0, 60, 20), ctx, "connectivity")
        assert stdscr.calls
    finally:
        connectivity_mode.cancel_forget()
        connectivity_mode.stop_browsing()


def test_sessions_draw_during_rename(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    sessions_mode.start_naming(1, "old name")
    try:
        ctx = _ctx(cfg, active_module="sessions")
        stdscr = _FakeStdscr()
        sessions_mode.draw(stdscr, (0, 0, 40, 10), ctx, "sessions")
        assert stdscr.calls
    finally:
        sessions_mode.handle_naming_key(27)  # Escape — the real cancel path


def test_sysmon_draw_during_nice_edit(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    sysmon_mode.start_nice_edit("some-window-id", pid=1234, current=0)
    try:
        ctx = _ctx(cfg, active_module="sysmon")
        stdscr = _FakeStdscr()
        sysmon_mode.draw(stdscr, (0, 0, 60, 20), ctx, "sysmon")
        assert stdscr.calls
    finally:
        sysmon_mode.handle_nice_key(27)  # Escape — the real cancel path


def test_media_draw_with_a_player_expanded(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    # No public setter for the expanded bus name (only collapse()) —
    # same module-global pattern connectivity.py/sessions.py/sysmon.py
    # expose through start_*()/is_*() pairs, just without one here
    # since nothing outside media.py itself ever needed to set it
    # directly until now.
    media_mode._expanded_bus_name = "org.mpris.MediaPlayer2.some_player"
    try:
        ctx = _ctx(cfg, active_module="media")
        stdscr = _FakeStdscr()
        media_mode.draw(stdscr, (0, 0, 60, 20), ctx, "media")
        assert stdscr.calls
    finally:
        media_mode.collapse()


def test_control_draw_with_an_action_error(tmp_path, monkeypatch):
    # Packaged default ships every [[control.toggle]] commented out —
    # a minimal hand-built ctx (same idiom test_control_module.py's
    # own _fake_ctx already uses) is the only way to reach this row
    # kind at all.
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "gammastep"},
                           {"name": "off", "command": "pkill -x gammastep"}]}]

    class _StatusWithActionError:
        def get(self, name):
            return None

        def get_error(self, name):
            return None

        def get_action_error(self, name):
            return "GeoClue2 provider is not installed!" if name == "toggle:0" else None

        def is_pending(self, name, key):
            return False

    ctx = SimpleNamespace(
        config=SimpleNamespace(control_toggles=toggles),
        status=_StatusWithActionError(),
        theme={"text": 0, "urgent": 0, "border": 0, "border_selected": 0, "selected": 0, "accent": 0},
        control_colors={},
        selected_id=None,
        active_module="control",
    )
    stdscr = _FakeStdscr()

    control_mode.draw(stdscr, (0, 0, 30, 10), ctx, "control")

    assert stdscr.calls


def test_resize_mode_editing_highlight(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    resize = resize_mode.ResizeState()
    resize_mode.enter_edit_mode(resize)
    box = cfg.layout.boxes[0]
    resize_mode.enter_box_editing(resize, box)

    stdscr = _FakeStdscr()
    resize_mode.draw_editing_highlight(stdscr, (0, 0, 30, 10), cfg.theme)

    assert stdscr.calls


def test_help_mode_draw_every_page(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    raw_keys = config_module.get_raw_navigation_keys()
    raw_power_menu_actions = config_module.get_raw_power_menu_actions()
    raw_theme_values = config_module.get_raw_theme_values()

    for page in ("help", "resize", "colors"):
        state = help_mode.HelpState(active=True, page=page)
        stdscr = _FakeStdscr()

        help_mode.draw(stdscr, 200, 55, cfg.theme, state, raw_keys, raw_power_menu_actions, raw_theme_values)

        assert stdscr.calls


def test_draw_status_line(tmp_path, monkeypatch):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    stdscr = _FakeStdscr()

    render_utils.draw_status_line(stdscr, 200, "Saved preset 1", cfg.theme.get("accent", 0))

    assert stdscr.calls
