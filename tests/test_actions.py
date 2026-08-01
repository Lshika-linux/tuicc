"""Tests for actions.py — BASE_HANDLERS, with a mock provider so no real
WM connection is needed.
"""

import subprocess
from types import SimpleNamespace

from tuicc.actions import BASE_HANDLERS, ActionContext, spawn_detached


class _FakeProvider:
    def __init__(self):
        self.focused_region = None
        self.focused_window = None

    def focus_region(self, region_id):
        self.focused_region = region_id

    def focus_window(self, window_id):
        self.focused_window = window_id


def test_region_handler_calls_focus_region():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, connectivity=None)
    item = SimpleNamespace(focus_target="workspace-3")

    should_exit, pending = BASE_HANDLERS["region"](ctx, item, cfg=None)

    assert provider.focused_region == "workspace-3"
    assert should_exit is True
    assert pending is None


def test_window_handler_calls_focus_window():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, connectivity=None)
    item = SimpleNamespace(focus_target="window-42")

    should_exit, pending = BASE_HANDLERS["window"](ctx, item, cfg=None)

    assert provider.focused_window == "window-42"
    assert should_exit is True
    assert pending is None


def test_base_handlers_has_exactly_region_and_window():
    assert set(BASE_HANDLERS.keys()) == {"region", "window"}


# ---------- spawn_detached ----------

def _fake_popen(calls, pid=4242):
    def popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(pid=pid)
    return popen


def test_spawn_detached_shell_true_passes_raw_string(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached("echo hi && echo bye", shell_true=True)

    cmd, kwargs = calls[0]
    assert cmd == "echo hi && echo bye"
    assert kwargs["shell"] is True


def test_spawn_detached_shell_false_splits_into_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached("firefox --private-window", shell_true=False)

    cmd, kwargs = calls[0]
    assert cmd == ["firefox", "--private-window"]
    assert kwargs["shell"] is False


def test_spawn_detached_defaults_to_shell_false(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached("kitty")

    cmd, kwargs = calls[0]
    assert cmd == ["kitty"]
    assert kwargs["shell"] is False


def test_spawn_detached_returns_the_process_pid(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _fake_popen([], pid=1234))

    assert spawn_detached("kitty") == 1234
