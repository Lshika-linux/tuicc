"""Tests for modules/control.py's handle() — with a fake StatusWorker
so no real subprocess I/O is needed, same pattern as
test_connectivity_handlers.py's _FakeConnectivity.
"""

from types import SimpleNamespace

from tuicc.actions import ActionContext
from tuicc.modules.control import handle


class _FakeStatus:
    def __init__(self, snapshots=None):
        self._snapshots = snapshots or {}
        self.request_action_calls = []

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def request_action(self, domain_name, action_name, arg, pending_key=None):
        self.request_action_calls.append((domain_name, action_name, arg, pending_key))


def _toggle(states):
    return {"label": "X", "shell_true": False, "states": states}


def test_handle_advances_from_off_to_on():
    cfg = SimpleNamespace(control_toggles=[
        _toggle([{"name": "on", "status_command": "p", "command": "c1"},
                 {"name": "off", "status_command": None, "command": "c2"}]),
    ])
    status = _FakeStatus(snapshots={"toggle:0": "off"})
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="control:0")

    should_dismiss, pending = handle(ctx, item, cfg)

    assert should_dismiss is False
    assert pending is None
    assert status.request_action_calls == [("toggle:0", "advance", "on", 0)]


def test_handle_wraps_around_an_n_state_cycle():
    cfg = SimpleNamespace(control_toggles=[
        _toggle([
            {"name": "power-saver", "status_command": "a", "command": "ca"},
            {"name": "balanced", "status_command": "b", "command": "cb"},
            {"name": "performance", "status_command": None, "command": "cc"},
        ]),
    ])
    status = _FakeStatus(snapshots={"toggle:0": "performance"})
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="control:0")

    handle(ctx, item, cfg)

    assert status.request_action_calls == [("toggle:0", "advance", "power-saver", 0)]


def test_handle_falls_back_to_first_state_when_current_is_unknown():
    # StatusWorker.get() returns None before the first poll lands, or
    # after a poll error — advancing "from unknown" lands on the first
    # configured state, see control.next_state_name's own docstring.
    cfg = SimpleNamespace(control_toggles=[
        _toggle([{"name": "on", "status_command": "p", "command": "c1"},
                 {"name": "off", "status_command": None, "command": "c2"}]),
    ])
    status = _FakeStatus(snapshots={})
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="control:0")

    handle(ctx, item, cfg)

    assert status.request_action_calls == [("toggle:0", "advance", "on", 0)]


def test_handle_uses_the_toggle_index_as_the_pending_key_not_the_target_name():
    # Only one advance meaningfully in flight per toggle at a time —
    # same reasoning audio's set_volume uses pending_key for (see
    # status_worker.py's request_action docstring).
    cfg = SimpleNamespace(control_toggles=[
        _toggle([{"name": "on", "status_command": "p", "command": "c1"},
                 {"name": "off", "status_command": None, "command": "c2"}]),
        _toggle([{"name": "on", "status_command": "p", "command": "c1"},
                 {"name": "off", "status_command": None, "command": "c2"}]),
    ])
    status = _FakeStatus(snapshots={"toggle:1": "off"})
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="control:1")

    handle(ctx, item, cfg)

    domain_name, action_name, target_name, pending_key = status.request_action_calls[0]
    assert domain_name == "toggle:1"
    assert pending_key == 1  # the toggle's own index, not target_name ("on")
