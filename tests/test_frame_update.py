"""Tests for frame_update.py's reset_ui_state() and update_frame()
itself. update_frame() was assumed untestable without curses/live I/O
(see this module's own docstring) — on inspection the only real I/O
touchpoints are a handful of collaborator objects (stdscr, provider,
status_worker, the D-Bus agents, cava_reader) that are cheap to fake,
same pattern test_pending_moves.py's own _FakeProvider and
test_draw_smoke.py's FakeStdscr already established elsewhere in this
suite. Not exhaustive line coverage — one test per genuinely distinct
behavior (a focus transition, a stale-selection recovery, a pending
D-Bus request, ...), not every branch.
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from i3ipc import Con

from tuicc.frame_update import reset_ui_state, update_frame
from tuicc.loop_state import LoopState
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules.launcher import LauncherState
from tuicc.resize_mode import ResizeState, SpawnPickerState, enter_edit_mode
from tuicc.help_mode import HelpState
from tuicc.model import WMState, Region
from tuicc.actions import ActionContext
from tuicc.app_setup import AppContext
from tuicc.procmon import PidFeed
from tuicc.providers.sway import parse_tree
from tuicc import pending_moves
from tuicc.pending_moves import PendingMovesQueue, MOVE_TIMEOUT_SECONDS
from tuicc.connectivity.model import WifiNetwork

from _curses_stub import FakeStdscr
from _fresh_install_helpers import load_packaged_default_config, _AllDomainsExceptWeather

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeMailbox:
    """WifiAgent/BluezAgent's own .mailbox — see agent_mailbox.py's
    AgentMailbox for the real, thread-safe shape. Only the two methods
    update_frame() actually reads."""
    def __init__(self, pending=False, request=None):
        self._pending = pending
        self._request = request

    def has_pending(self):
        return self._pending

    def get_request(self):
        return self._request


class _FakeAgent:
    def __init__(self, mailbox=None, error=None):
        self.cancel_current_calls = 0
        self.mailbox = mailbox if mailbox is not None else _FakeMailbox()
        self._error = error

    def cancel_current(self):
        self.cancel_current_calls += 1

    def get_error(self):
        return self._error


def _dirty_loop_state():
    return LoopState(
        mode_stack=["normal", "help", "help_colors"],
        selected_id="control:0",
        active_module="control",
    )


def _reset(loop_state, resize=None, spawn_picker=None, help_state=None, launcher=None, wifi_agent=None, bluez_agent=None):
    reset_ui_state(
        loop_state,
        resize if resize is not None else ResizeState(),
        spawn_picker if spawn_picker is not None else SpawnPickerState(),
        help_state if help_state is not None else HelpState(),
        launcher if launcher is not None else LauncherState(),
        wifi_agent if wifi_agent is not None else _FakeAgent(),
        bluez_agent,
    )


def test_reset_ui_state_clears_mode_stack():
    loop_state = _dirty_loop_state()
    _reset(loop_state)
    assert loop_state.mode_stack == ["normal"]


def test_reset_ui_state_clears_selection_and_forces_sidebar_active():
    # active_module="sidebar", not None or left as-is: frame_update.py's
    # own stale-selection recovery treats selected_id=None as deliberate
    # (skips recovery) whenever active_module has zero nav items — the
    # trap "launcher" (a permanently empty module) would fall into.
    loop_state = _dirty_loop_state()
    _reset(loop_state)
    assert loop_state.selected_id is None
    assert loop_state.active_module == "sidebar"


def test_reset_ui_state_ends_an_in_progress_resize_session():
    loop_state = _dirty_loop_state()
    resize = ResizeState()
    enter_edit_mode(resize)
    assert resize.active is True

    _reset(loop_state, resize=resize)

    assert resize.active is False


def test_reset_ui_state_closes_an_open_spawn_picker():
    loop_state = _dirty_loop_state()
    spawn_picker = SpawnPickerState(active=True, choices=["control", "media"])

    _reset(loop_state, spawn_picker=spawn_picker)

    assert spawn_picker.active is False
    assert spawn_picker.choices == []


def test_reset_ui_state_closes_the_help_panel():
    loop_state = _dirty_loop_state()
    help_state = HelpState(active=True, page="colors")

    _reset(loop_state, help_state=help_state)

    assert help_state.active is False


def test_reset_ui_state_cancels_a_connectivity_passphrase_entry():
    connectivity_mode.start_passphrase_entry("some-network")
    assert connectivity_mode.is_entering_passphrase() is True

    _reset(_dirty_loop_state())

    assert connectivity_mode.is_entering_passphrase() is False


def test_reset_ui_state_stops_connectivity_browsing():
    connectivity_mode.start_browsing("wifi")
    assert connectivity_mode.is_browsing() is True

    _reset(_dirty_loop_state())

    assert connectivity_mode.is_browsing() is False


def test_reset_ui_state_cancels_an_in_progress_session_rename():
    sessions_mode.start_naming(1, "old name")
    assert sessions_mode.is_naming() is True

    _reset(_dirty_loop_state())

    assert sessions_mode.is_naming() is False


def test_reset_ui_state_cancels_an_in_progress_nice_edit():
    sysmon_mode.start_nice_edit("42", pid=1234, current=0)
    assert sysmon_mode.is_editing_nice() is True

    _reset(_dirty_loop_state())

    assert sysmon_mode.is_editing_nice() is False


# ---------- wifi_agent/bluez_agent.cancel_current() ----------
# Found live: cancel_passphrase_entry()/cancel_pairing_confirm() alone
# only clear connectivity.py's own UI-side display state. The D-Bus
# agent's mailbox stays pending (nobody ever actually replied to iwd/
# bluez's own request), so update_frame()'s own per-frame mailbox
# check pushes connectivity_passphrase/pairing right back onto
# mode_stack the very next frame, undoing the reset entirely.

def test_reset_ui_state_cancels_the_pending_wifi_agent_request():
    wifi_agent = _FakeAgent()
    _reset(_dirty_loop_state(), wifi_agent=wifi_agent)
    assert wifi_agent.cancel_current_calls == 1


def test_reset_ui_state_cancels_the_pending_bluez_agent_request():
    bluez_agent = _FakeAgent()
    _reset(_dirty_loop_state(), bluez_agent=bluez_agent)
    assert bluez_agent.cancel_current_calls == 1


def test_reset_ui_state_tolerates_no_bluez_agent():
    # bluez_agent is None on a setup with no bluetooth adapter at all
    # (see app_setup.py) — must not crash.
    _reset(_dirty_loop_state(), bluez_agent=None)


def test_reset_ui_state_is_a_safe_no_op_when_nothing_was_in_progress():
    # Every function reset_ui_state() calls is safe to call
    # unconditionally — this just confirms the whole thing doesn't
    # raise when there's genuinely nothing to cancel.
    loop_state = LoopState()
    _reset(loop_state)
    assert loop_state.mode_stack == ["normal"]


# ==================== update_frame() ====================
# Real function, fake collaborators — see this file's own docstring.

class _FakeProvider:
    def __init__(self, state=None, self_focused_value=False):
        self.state = state if state is not None else WMState()
        self.self_focused_value = self_focused_value
        self.moved = []
        self.floated = []
        self.focus_self_calls = 0
        self.no_focus_next_window_calls = []
        self.resolved_pids = {}

    def get_state(self):
        return self.state

    def self_focused(self):
        return self.self_focused_value

    def resolve_pid(self, window_id):
        return self.resolved_pids.get(window_id)

    # ---- pending_moves.process()/promote_restore_queue()'s own needs ----
    def move_window_to_region(self, window_id, region_id):
        self.moved.append((window_id, region_id))

    def set_floating_geometry(self, window_id, region_id, rect):
        self.floated.append((window_id, region_id, rect))

    def focus_self(self, fullscreen=False, force_relayout=False):
        self.focus_self_calls += 1

    def no_focus_next_window(self, pid):
        self.no_focus_next_window_calls.append(pid)


class _FakeStatusWorker:
    """A mutable sibling of _fresh_install_helpers.FreshInstallStatus —
    that one is a fixed "fresh install" shape; update_frame()'s own
    tests need per-scenario control over what's pending/erroring."""
    def __init__(self, snapshots=None, errors=None, pending=None, action_errors=None):
        self._snapshots = dict(snapshots or {})
        self._errors = errors or {}
        self._pending = pending or set()  # {(domain, key), ...}
        self._action_errors = action_errors or {}

    def set(self, name, value):
        self._snapshots[name] = value

    def get(self, name):
        return self._snapshots.get(name)

    def get_error(self, name):
        return self._errors.get(name)

    def is_pending(self, name, key):
        return (name, key) in self._pending

    def get_action_error(self, name):
        return self._action_errors.get(name)

    def get_action_error_for(self, name, key):
        return self._action_errors.get((name, key))

    def has_pending(self):
        return bool(self._pending)

    def domain_names(self):
        # Everything except "weather" — same "registered by default,
        # opt-in only" shape _fresh_install_helpers.FreshInstallStatus
        # already uses; the packaged default config ships weather
        # commented out, so rwb.py's own domain_names() check must see
        # it as genuinely absent, not just unpolled.
        return _AllDomainsExceptWeather()


class _FakeCavaReader:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running

    def get_error(self):
        return None

    def get_frame(self):
        return None


def _app(tmp_path, monkeypatch, provider=None, status=None, wifi_agent=None, bluez_agent=None, action_ctx=None):
    cfg = load_packaged_default_config(tmp_path, monkeypatch)
    theme_pairs = {
        "accent": 1, "text": 2, "urgent": 3, "border": 4,
        "border_selected": 5, "selected": 6, "warning": 7, "background": 0,
    }
    provider = provider if provider is not None else _FakeProvider()
    status = status if status is not None else _FakeStatusWorker()
    wifi_agent = wifi_agent if wifi_agent is not None else _FakeAgent()
    action_ctx = action_ctx if action_ctx is not None else ActionContext(provider=provider, status=status)
    return AppContext(
        cfg=cfg,
        theme_pairs=theme_pairs,
        control_colors={},
        provider=provider,
        wifi_agent=wifi_agent,
        bluez_agent=bluez_agent,
        pid_feed=PidFeed(),
        status_worker=status,
        cava_reader=_FakeCavaReader(),
        action_ctx=action_ctx,
        wm_config=None,
    )


def _frame(app, loop_state, moves=None, resize=None, spawn_picker=None, help_state=None, launcher=None):
    return update_frame(
        FakeStdscr(), app, loop_state,
        resize if resize is not None else ResizeState(),
        spawn_picker if spawn_picker is not None else SpawnPickerState(),
        help_state if help_state is not None else HelpState(),
        launcher if launcher is not None else LauncherState(),
        moves if moves is not None else PendingMovesQueue(),
    )


def _sway_fixture_state():
    with open(FIXTURES / "sway_basic.json") as f:
        return parse_tree(Con(json.load(f), None, None))


def test_update_frame_baseline_builds_a_frame_result(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    loop_state = LoopState()

    frame = _frame(app, loop_state)

    assert isinstance(frame.ordered, list)
    assert frame.ctx.state is app.provider.state
    assert frame.term_width > 0 and frame.term_height > 0


def test_focused_region_transition_resets_and_updates_tracking(tmp_path, monkeypatch):
    state = WMState(regions=[Region(id="2", name="2", windows=[], focused=True)], focused_region_id="2")
    provider = _FakeProvider(state=state)
    app = _app(tmp_path, monkeypatch, provider=provider)
    loop_state = LoopState(selected_id="control:0", last_focused_region_id="1")

    _frame(app, loop_state)

    assert loop_state.origin_region_id == "1"
    assert loop_state.last_focused_region_id == "2"


def test_expect_focus_reclaim_suppresses_the_transition_reset(tmp_path, monkeypatch):
    state = WMState(regions=[Region(id="2", name="2", windows=[], focused=True)], focused_region_id="2")
    provider = _FakeProvider(state=state)
    app = _app(tmp_path, monkeypatch, provider=provider)
    # "sidebar:2" is a genuinely valid selection this frame (matches the
    # focused region) — chosen so the unrelated stale-selection recovery
    # block can't accidentally make this test pass for the wrong reason.
    loop_state = LoopState(selected_id="sidebar:2", last_focused_region_id="1", expect_focus_reclaim=True)

    _frame(app, loop_state)

    assert loop_state.expect_focus_reclaim is False  # consumed
    assert loop_state.last_focused_region_id == "2"  # still tracked, even though self-inflicted


def test_self_focused_transition_triggers_reset_ui_state(tmp_path, monkeypatch):
    provider = _FakeProvider(self_focused_value=True)
    app = _app(tmp_path, monkeypatch, provider=provider)
    loop_state = LoopState(mode_stack=["normal", "help"], self_was_focused=False)

    _frame(app, loop_state)

    assert loop_state.mode_stack == ["normal"]
    assert loop_state.self_was_focused is True


def test_self_focused_staying_true_does_not_retrigger_reset(tmp_path, monkeypatch):
    provider = _FakeProvider(self_focused_value=True)
    app = _app(tmp_path, monkeypatch, provider=provider)
    loop_state = LoopState(mode_stack=["normal", "help"], self_was_focused=True)

    _frame(app, loop_state)

    assert loop_state.mode_stack == ["normal", "help"]  # untouched — reset_ui_state was not called


def test_timed_out_pending_move_sets_a_failure_toast(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    loop_state = LoopState()
    moves = PendingMovesQueue(entries=[{
        "known_ids": set(), "target_region": "2",
        "started_at": time.monotonic() - MOVE_TIMEOUT_SECONDS - 1,
        "pid": 999999, "app_id": "obsidian", "root_pid": 999999, "known_pids": {999999},
    }])

    _frame(app, loop_state, moves=moves)

    assert loop_state.resize_message is not None
    assert moves.entries == []


def test_restore_queue_spawn_failure_sets_a_failure_toast(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: None)
    provider = _FakeProvider()
    action_ctx = ActionContext(
        provider=provider, status=_FakeStatusWorker(),
        restore_queue=[{"cmdline": ["/nonexistent"], "target_region": "2", "app_id": "obsidian"}],
    )
    app = _app(tmp_path, monkeypatch, provider=provider, action_ctx=action_ctx)
    loop_state = LoopState()

    _frame(app, loop_state)

    assert loop_state.resize_message is not None
    assert action_ctx.restore_queue == []


def test_stale_selection_recovers_to_the_focused_region(tmp_path, monkeypatch):
    state = _sway_fixture_state()
    provider = _FakeProvider(state=state)
    app = _app(tmp_path, monkeypatch, provider=provider)
    # active_module="sidebar" (a module with real items this frame) —
    # not None/empty, or the intentionally_unselected guard would treat
    # this exactly like a deliberate Left/Right jump onto an empty
    # module and skip recovery entirely (see that guard's own comment).
    loop_state = LoopState(selected_id="sidebar:99", active_module="sidebar")  # not a real item this frame

    _frame(app, loop_state)

    assert loop_state.selected_id == f"sidebar:{state.focused_region_id}"


def test_intentionally_unselected_stays_unselected(tmp_path, monkeypatch):
    # "launcher" permanently has zero nav_items — selected_id=None here
    # is deliberate (a Left/Right jump onto an empty module), not stale.
    app = _app(tmp_path, monkeypatch)
    loop_state = LoopState(selected_id=None, active_module="launcher")

    _frame(app, loop_state)

    assert loop_state.selected_id is None


def test_wifi_mailbox_pending_pushes_the_passphrase_claim(tmp_path, monkeypatch):
    mailbox = _FakeMailbox(pending=True, request=SimpleNamespace(ssid="MyWifi"))
    wifi_agent = _FakeAgent(mailbox=mailbox)
    app = _app(tmp_path, monkeypatch, wifi_agent=wifi_agent)
    loop_state = LoopState()

    try:
        _frame(app, loop_state)

        assert loop_state.mode_stack[-1] == "connectivity_passphrase"
        assert connectivity_mode.is_entering_passphrase() is True
    finally:
        connectivity_mode.cancel_passphrase_entry()


def test_connectivity_browsing_selection_follows_the_list_emptying_and_refilling(tmp_path, monkeypatch):
    status = _FakeStatusWorker(snapshots={"wifi": []})
    app = _app(tmp_path, monkeypatch, status=status)
    connectivity_mode.start_browsing("wifi")
    try:
        loop_state = LoopState(mode_stack=["normal", "connectivity_browsing"], selected_id="connectivity:wifi:somenet")

        _frame(app, loop_state)
        assert loop_state.selected_id == "connectivity:wifi:empty"

        status.set("wifi", [WifiNetwork(ssid="MyWifi", connected=False)])
        _frame(app, loop_state)
        assert loop_state.selected_id == "connectivity:wifi:MyWifi"
    finally:
        connectivity_mode.stop_browsing()
