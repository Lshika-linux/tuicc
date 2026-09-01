"""Tests for actions.py — BASE_HANDLERS, dispatch_action, and
handle_pending_confirm, with a mock provider so no real WM connection
is needed.
"""

import subprocess
from types import SimpleNamespace

import tuicc.actions as actions_module
from tuicc.actions import (
    BASE_HANDLERS,
    ActionContext,
    spawn_detached,
    dispatch_action,
    handle_pending_confirm,
)


class _FakeConfig:
    """Just enough of Config to exercise handle_pending_confirm's key lookups."""
    keybinds = {"confirm_yes": ord("y"), "confirm_no": ord("n"), "confirm": 10}


_cfg = _FakeConfig()


class _FakeProvider:
    def __init__(self):
        self.focused_region = None
        self.focused_window = None
        self.closed_windows = []
        self.regions = []
        self.focused_region_id = None
        self.calls = []

    def dismiss_self(self):
        self.calls.append("dismiss_self")

    def focus_region(self, region_id):
        self.calls.append("focus_region")
        self.focused_region = region_id

    def focus_window(self, window_id):
        self.calls.append("focus_window")
        self.focused_window = window_id

    def close_window(self, window_id):
        self.closed_windows.append(window_id)

    def get_state(self):
        return SimpleNamespace(regions=self.regions, focused_region_id=self.focused_region_id)


def test_region_handler_calls_focus_region():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, status=None)
    item = SimpleNamespace(focus_target="workspace-3")

    should_exit, pending = BASE_HANDLERS["region"](ctx, item, cfg=None)

    assert provider.focused_region == "workspace-3"
    assert provider.calls == ["dismiss_self", "focus_region"]
    assert should_exit is True
    assert pending is None


def test_window_handler_calls_focus_window():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, status=None)
    item = SimpleNamespace(focus_target="window-42")

    should_exit, pending = BASE_HANDLERS["window"](ctx, item, cfg=None)

    assert provider.focused_window == "window-42"
    assert provider.calls == ["dismiss_self", "focus_window"]
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


def test_spawn_detached_accepts_a_presplit_list_unchanged(monkeypatch):
    # session.py's saved cmdline is already a list (from
    # /proc/<pid>/cmdline) — must pass through as-is, not get
    # joined-then-resplit, which would mangle an arg containing a space.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached(["kitty", "--title", "has a space"], shell_true=False)

    cmd, kwargs = calls[0]
    assert cmd == ["kitty", "--title", "has a space"]
    assert kwargs["shell"] is False


def test_spawn_detached_no_log_path_uses_devnull(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached("kitty")

    _, kwargs = calls[0]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_spawn_detached_log_path_redirects_stdout_and_stderr(monkeypatch, tmp_path):
    # See spawn_detached's docstring and
    # CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash: a saved
    # session cmdline that crashes on relaunch (missing env a wrapper
    # script would normally set up) looks identical to "never started"
    # from the outside unless its output is captured instead of DEVNULL.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    log_path = tmp_path / "logs" / "restore_obsidian_123.log"

    spawn_detached("kitty", log_path=log_path)

    _, kwargs = calls[0]
    assert log_path.parent.is_dir()  # created on demand, same as SESSIONS_DIR
    assert kwargs["stdout"].name == str(log_path)
    assert kwargs["stdout"] is kwargs["stderr"]  # same fd, chronological interleave


# ---------- spawn_detached: Popen itself failing ----------
# Found live: a session-restore entry whose saved cmdline no longer
# resolved to a real executable (Electron/NixOS rewriting its own argv
# — see pending_moves.py's own _spawn_failure_message docstring) raised
# FileNotFoundError straight out of subprocess.Popen(), uncaught,
# crashing tuicc's entire main loop rather than just that one spawn.

def _raising_popen(exc):
    def popen(cmd, **kwargs):
        raise exc
    return popen


def test_spawn_detached_returns_none_when_popen_raises_oserror(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _raising_popen(FileNotFoundError(2, "No such file or directory")))

    assert spawn_detached("/nonexistent/path/to/nothing") is None


def test_spawn_detached_writes_the_exception_into_log_path_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", _raising_popen(FileNotFoundError(2, "No such file or directory")))
    log_path = tmp_path / "logs" / "restore_obsidian_123.log"

    result = spawn_detached("/nonexistent/path/to/nothing", log_path=log_path)

    assert result is None
    assert "No such file or directory" in log_path.read_text()


# ---------- spawn_detached: env ----------

def test_spawn_detached_no_env_passes_none_to_popen(monkeypatch):
    # env=None to Popen means "inherit the current process's
    # environment exactly," identical to every call site before this
    # parameter existed — confirms zero behavior change by default.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))

    spawn_detached("kitty")

    _, kwargs = calls[0]
    assert kwargs["env"] is None


def test_spawn_detached_env_is_layered_over_current_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    monkeypatch.setattr(actions_module.os, "environ", {"PATH": "/usr/bin", "HOME": "/home/rafi"})

    spawn_detached("obsidian", env={"NODE_PATH": "/nix/store/.../node_modules"})

    _, kwargs = calls[0]
    assert kwargs["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/home/rafi",
        "NODE_PATH": "/nix/store/.../node_modules",
    }


def test_spawn_detached_captured_env_cannot_override_always_live_keys(monkeypatch):
    # A session saved today and loaded after a relogin/reboot must not
    # carry yesterday's now-nonexistent WAYLAND_DISPLAY into the
    # respawned process — the live value always wins.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    monkeypatch.setattr(actions_module.os, "environ", {"WAYLAND_DISPLAY": "wayland-1"})

    spawn_detached("obsidian", env={"WAYLAND_DISPLAY": "wayland-STALE", "NODE_PATH": "/x"})

    _, kwargs = calls[0]
    assert kwargs["env"]["WAYLAND_DISPLAY"] == "wayland-1"
    assert kwargs["env"]["NODE_PATH"] == "/x"


def test_spawn_detached_always_live_key_absent_from_current_env_is_dropped(monkeypatch):
    # Captured env has SWAYSOCK (saved on sway); this session is i3, no
    # SWAYSOCK in the current environment at all — must not leak the
    # stale sway value through, since there's no live one to prefer.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    monkeypatch.setattr(actions_module.os, "environ", {"PATH": "/usr/bin"})

    spawn_detached("obsidian", env={"SWAYSOCK": "/run/sway-STALE.sock"})

    _, kwargs = calls[0]
    assert "SWAYSOCK" not in kwargs["env"]


# ---------- dispatch_action ----------

def test_dispatch_action_runs_the_matching_handler():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, status=None)
    item = SimpleNamespace(target_kind="region", focus_target="workspace-3")

    should_dismiss, pending = dispatch_action(ctx, BASE_HANDLERS, item, cfg=None)

    assert provider.focused_region == "workspace-3"
    assert should_dismiss is True
    assert pending is None


def test_dispatch_action_unknown_target_kind_returns_false_none():
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    item = SimpleNamespace(target_kind="nonexistent_kind")

    should_dismiss, pending = dispatch_action(ctx, BASE_HANDLERS, item, cfg=None)

    assert (should_dismiss, pending) == (False, None)


def test_dispatch_action_propagates_pending_from_handler():
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    item = SimpleNamespace(target_kind="confirm_kind")
    handlers = {"confirm_kind": lambda ctx, item, cfg: (False, {"command": "rm -rf /", "shell_true": False})}

    should_dismiss, pending = dispatch_action(ctx, handlers, item, cfg=None)

    assert pending == {"command": "rm -rf /", "shell_true": False}
    assert should_dismiss is False


# ---------- handle_pending_confirm ----------

def test_handle_pending_confirm_yes_command_shaped_spawns_it(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    pending = {"command": "swaylock", "shell_true": False, "dismiss_after_confirm": True}

    should_dismiss, new_pending = handle_pending_confirm(ctx, pending, ord("y"), _cfg)

    assert calls[0][0] == ["swaylock"]
    assert (should_dismiss, new_pending) == (True, None)


def test_handle_pending_confirm_confirm_key_also_answers_yes(monkeypatch):
    # Enter (cfg.keybinds["confirm"]) is accepted as an alternate to the
    # dedicated confirm_yes key — "yes" is itself a kind of confirm, and
    # Enter already means confirm everywhere else in tuicc. confirm_no
    # gets no such alternate (see the docstring).
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    pending = {"command": "swaylock", "shell_true": False, "dismiss_after_confirm": True}

    should_dismiss, new_pending = handle_pending_confirm(ctx, pending, _cfg.keybinds["confirm"], _cfg)

    assert calls[0][0] == ["swaylock"]
    assert (should_dismiss, new_pending) == (True, None)


def test_handle_pending_confirm_yes_restore_shaped_extends_restore_queue():
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    pending = {
        "restore_entries": [{"cmdline": ["kitty"]}],
        "dismiss_after_confirm": False,
    }

    should_dismiss, new_pending = handle_pending_confirm(ctx, pending, ord("y"), _cfg)

    assert ctx.restore_queue == [{"cmdline": ["kitty"]}]
    assert (should_dismiss, new_pending) == (False, None)


def test_handle_pending_confirm_yes_restore_shaped_sets_reselect_region_id():
    # See ActionContext.reselect_region_id's docstring — the confirm-
    # needed load path (kill_regions overlap) sets it here, once the
    # restore is actually committed, mirroring the direct no-confirm
    # path in sessions.py's handle_action.
    provider = _FakeProvider()
    provider.focused_region_id = "1"
    ctx = ActionContext(provider=provider, status=None)
    pending = {
        "restore_entries": [{"cmdline": ["kitty"]}],
        "dismiss_after_confirm": False,
    }

    handle_pending_confirm(ctx, pending, ord("y"), _cfg)

    assert ctx.reselect_region_id == "1"


def test_handle_pending_confirm_yes_restore_shaped_with_kill_regions_closes_matched_windows():
    provider = _FakeProvider()
    provider.regions = [
        SimpleNamespace(id="1", windows=[SimpleNamespace(id="w1"), SimpleNamespace(id="w2")]),
        SimpleNamespace(id="2", windows=[SimpleNamespace(id="w3")]),
    ]
    ctx = ActionContext(provider=provider, status=None)
    pending = {
        "restore_entries": [{"cmdline": ["kitty"]}],
        "kill_regions": ["1"],
        "dismiss_after_confirm": False,
    }

    handle_pending_confirm(ctx, pending, ord("y"), _cfg)

    assert provider.closed_windows == ["w1", "w2"]


def test_handle_pending_confirm_no_discards_without_running_anything(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(calls))
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    pending = {"command": "swaylock", "shell_true": False, "dismiss_after_confirm": True}

    should_dismiss, new_pending = handle_pending_confirm(ctx, pending, ord("n"), _cfg)

    assert calls == []
    assert (should_dismiss, new_pending) == (False, None)


def test_handle_pending_confirm_other_key_leaves_dialog_open_unchanged():
    ctx = ActionContext(provider=_FakeProvider(), status=None)
    pending = {"command": "swaylock", "shell_true": False, "dismiss_after_confirm": True}

    should_dismiss, new_pending = handle_pending_confirm(ctx, pending, ord("x"), _cfg)

    assert new_pending is pending
    assert should_dismiss is False
