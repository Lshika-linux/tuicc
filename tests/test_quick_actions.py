"""Tests for quick_actions.py — only the pure logic (handle). draw()
needs a real curses screen to exercise meaningfully, so it's left
untested here, same as other modules' drawing code.
"""

import subprocess
from types import SimpleNamespace

from tuicc.modules.quick_actions import handle


def test_handle_confirm_required_tags_pending_with_owning_module():
    # Regression: draw() only shows a pending_confirm dict if it's
    # tagged with its own module name — see the same test in
    # test_power_menu.py for why this matters.
    cfg = SimpleNamespace(quick_actions=[
        {"label": "Logout", "command": "swaymsg exit", "confirm": True, "shell_true": False},
    ])
    item = SimpleNamespace(id="quick_actions:0")

    should_dismiss, pending = handle(ctx=None, item=item, cfg=cfg)

    assert should_dismiss is False
    assert pending["module"] == "quick_actions"
    assert pending["command"] == "swaymsg exit"


def test_handle_confirm_pending_defaults_dismiss_after_confirm_true_when_absent():
    # A raw action dict with no "exit_after" key (e.g. from an older
    # in-memory literal) shouldn't crash — handle() uses .get(), same
    # default (True) as config.py's own parsing default.
    cfg = SimpleNamespace(quick_actions=[
        {"label": "Logout", "command": "swaymsg exit", "confirm": True, "shell_true": False},
    ])
    item = SimpleNamespace(id="quick_actions:0")

    _, pending = handle(ctx=None, item=item, cfg=cfg)

    assert pending["dismiss_after_confirm"] is True


def _fake_popen(calls, pid=4242):
    def popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(pid=pid)
    return popen


def test_handle_non_confirm_respects_exit_after_false(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    cfg = SimpleNamespace(quick_actions=[
        {"label": "Restart bar", "command": "killall -SIGUSR1 waybar",
         "confirm": False, "shell_true": False, "exit_after": False},
    ])
    item = SimpleNamespace(id="quick_actions:0")

    should_dismiss, pending = handle(ctx=None, item=item, cfg=cfg)

    assert should_dismiss is False
    assert pending is None
    assert calls  # spawn_detached still ran the command


def test_handle_non_confirm_defaults_exit_after_true_when_absent(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    cfg = SimpleNamespace(quick_actions=[
        {"label": "Lock", "command": "swaylock", "confirm": False, "shell_true": False},
    ])
    item = SimpleNamespace(id="quick_actions:0")

    should_dismiss, pending = handle(ctx=None, item=item, cfg=cfg)

    assert should_dismiss is True
    assert pending is None