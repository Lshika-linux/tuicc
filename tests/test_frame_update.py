"""Tests for frame_update.py's reset_ui_state() — the one piece of this
module genuinely testable without curses/live I/O (see the module's own
docstring for why update_frame() itself isn't). Shared by main.py's
do_dismiss() and this module's own provider.self_focused()-transition
detection, so it's worth covering directly now that two call sites
depend on it instead of one.
"""

from tuicc.frame_update import reset_ui_state
from tuicc.loop_state import LoopState
from tuicc.modules import connectivity as connectivity_mode
from tuicc.modules import sessions as sessions_mode
from tuicc.modules import sysmon as sysmon_mode
from tuicc.modules.launcher import LauncherState
from tuicc.resize_mode import ResizeState, SpawnPickerState, enter_edit_mode
from tuicc.help_mode import HelpState


class _FakeAgent:
    def __init__(self):
        self.cancel_current_calls = 0

    def cancel_current(self):
        self.cancel_current_calls += 1


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
