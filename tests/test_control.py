"""Tests for control.py's backend logic — the toggle/cycle unification
(a 2-state toggle and an N-state cycle are the same mechanism) found
while designing [[control.toggle]] with the user. probe_state's own
subprocess plumbing is tested directly; find_current_state/
next_state_name/run_state_command are tested against fakes so they
stay isolated from subprocess details, matching this repo's existing
split (see test_connectivity.py's iwd/bluez propagation tests for the
same pattern).
"""

import os
import subprocess

import pytest

from tuicc import control


# ---------- probe_state ----------

def test_probe_state_true_on_exit_zero(monkeypatch):
    def _run(cmd, **kwargs):
        assert cmd == ["pgrep", "-x", "gammastep"]
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", _run)
    assert control.probe_state("pgrep -x gammastep", shell_true=False) is True


def test_probe_state_false_on_nonzero_exit(monkeypatch):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=1)

    monkeypatch.setattr(subprocess, "run", _run)
    assert control.probe_state("pgrep -x gammastep", shell_true=False) is False


def test_probe_state_shell_true_passes_the_raw_string(monkeypatch):
    def _run(cmd, **kwargs):
        assert cmd == "pactl get-source-mute @DEFAULT_SOURCE@ | grep -q yes"
        assert kwargs["shell"] is True
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", _run)
    control.probe_state("pactl get-source-mute @DEFAULT_SOURCE@ | grep -q yes", shell_true=True)


def test_probe_state_propagates_real_failures(monkeypatch):
    # No-silent-failure (VISION.md R3/R5): a missing binary or timeout
    # must reach status_worker.StatusWorker's poll wrapper, not vanish
    # behind a bare except here — same fix already applied to
    # connectivity/iwd.py and bluez.py.
    def _raise(*args, **kwargs):
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(FileNotFoundError):
        control.probe_state("pgrep -x gammastep", shell_true=False)


# ---------- find_current_state ----------

def _states_2way():
    return [
        {"name": "on", "status_command": "on-probe", "command": "turn-on"},
        {"name": "off", "status_command": None, "command": "turn-off"},
    ]


def test_find_current_state_returns_first_matching_state(monkeypatch):
    monkeypatch.setattr(control, "probe_state", lambda cmd, shell_true: cmd == "on-probe")
    assert control.find_current_state(_states_2way(), shell_true=False) == "on"


def test_find_current_state_falls_back_to_implicit_last_state(monkeypatch):
    monkeypatch.setattr(control, "probe_state", lambda cmd, shell_true: False)
    assert control.find_current_state(_states_2way(), shell_true=False) == "off"


def test_find_current_state_nway_cycle(monkeypatch):
    states = [
        {"name": "power-saver", "status_command": "is-power-saver", "command": "set power-saver"},
        {"name": "balanced", "status_command": "is-balanced", "command": "set balanced"},
        {"name": "performance", "status_command": None, "command": "set performance"},
    ]
    monkeypatch.setattr(control, "probe_state", lambda cmd, shell_true: cmd == "is-balanced")
    assert control.find_current_state(states, shell_true=True) == "balanced"


def test_find_current_state_raises_when_every_state_has_a_probe_and_none_match(monkeypatch):
    # Every state explicitly probed (no implicit last) — a real
    # mismatch between status_command and the tool's actual output,
    # not something to silently guess past.
    states = [
        {"name": "a", "status_command": "probe-a", "command": "cmd-a"},
        {"name": "b", "status_command": "probe-b", "command": "cmd-b"},
    ]
    monkeypatch.setattr(control, "probe_state", lambda cmd, shell_true: False)
    with pytest.raises(RuntimeError):
        control.find_current_state(states, shell_true=False)


# ---------- next_state_name ----------

def test_next_state_name_advances_by_one():
    states = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert control.next_state_name(states, "a") == "b"
    assert control.next_state_name(states, "b") == "c"


def test_next_state_name_wraps_around():
    states = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert control.next_state_name(states, "c") == "a"


def test_next_state_name_binary_toggle_is_just_a_2_state_wrap():
    states = [{"name": "off"}, {"name": "on"}]
    assert control.next_state_name(states, "off") == "on"
    assert control.next_state_name(states, "on") == "off"


def test_next_state_name_falls_back_to_first_when_current_unknown():
    states = [{"name": "a"}, {"name": "b"}]
    assert control.next_state_name(states, None) == "a"
    assert control.next_state_name(states, "not-a-real-state") == "a"


# ---------- run_state_command ----------

def test_run_state_command_spawns_the_matching_states_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        control, "_run_detached_detecting_quick_failure",
        lambda command, shell_true: calls.append((command, shell_true)),
    )
    # Confirmed on the very first check — no need to actually sleep/loop.
    monkeypatch.setattr(control, "find_current_state", lambda states, shell_true: "off")

    states = [
        {"name": "on", "status_command": "p", "command": "gammastep"},
        {"name": "off", "status_command": None, "command": "pkill -x gammastep"},
    ]
    control.run_state_command(states, "off", shell_true=False)

    assert calls == [("pkill -x gammastep", False)]


def test_run_state_command_raises_for_an_unknown_state_name():
    states = [{"name": "on", "command": "x"}]
    with pytest.raises(ValueError):
        control.run_state_command(states, "nonexistent", shell_true=False)


# ---------- run_state_command's confirmation wait ----------
# pending/blink (StatusWorker) is tied directly to how long this
# function takes to return — returning the instant the command is
# spawned, well before status_command can confirm the real effect,
# would make the module look "frozen" in between.

def test_run_state_command_returns_as_soon_as_confirmed(monkeypatch):
    monkeypatch.setattr(control, "_run_detached_detecting_quick_failure", lambda command, shell_true: None)
    calls = []

    def _find_current_state(states, shell_true):
        calls.append(1)
        return "off"  # confirmed on the very first check

    monkeypatch.setattr(control, "find_current_state", _find_current_state)
    states = [{"name": "on", "status_command": "p", "command": "a"},
              {"name": "off", "status_command": None, "command": "b"}]

    control.run_state_command(states, "off", shell_true=False, confirm_timeout=5.0)

    assert len(calls) == 1  # didn't keep polling once already confirmed


def test_run_state_command_keeps_polling_until_confirmed(monkeypatch):
    monkeypatch.setattr(control, "_run_detached_detecting_quick_failure", lambda command, shell_true: None)
    results = iter(["on", "on", "off"])  # takes 3 checks to actually converge
    monkeypatch.setattr(control, "find_current_state", lambda states, shell_true: next(results))
    states = [{"name": "on", "status_command": "p", "command": "a"},
              {"name": "off", "status_command": None, "command": "b"}]

    control.run_state_command(states, "off", shell_true=False, confirm_timeout=5.0)

    assert next(results, "exhausted") == "exhausted"  # all 3 were consumed


def test_run_state_command_gives_up_after_confirm_timeout(monkeypatch):
    monkeypatch.setattr(control, "_run_detached_detecting_quick_failure", lambda command, shell_true: None)
    # Never confirms — must not hang forever, just stop after the budget.
    monkeypatch.setattr(control, "find_current_state", lambda states, shell_true: "on")
    states = [{"name": "on", "status_command": "p", "command": "a"},
              {"name": "off", "status_command": None, "command": "b"}]

    control.run_state_command(states, "off", shell_true=False, confirm_timeout=0.25)  # returns, doesn't raise


def test_run_state_command_stops_immediately_if_status_matches_nothing(monkeypatch):
    monkeypatch.setattr(control, "_run_detached_detecting_quick_failure", lambda command, shell_true: None)
    calls = []

    def _find_current_state(states, shell_true):
        calls.append(1)
        raise RuntimeError("status_command output matched none of the configured states")

    monkeypatch.setattr(control, "find_current_state", _find_current_state)
    states = [{"name": "a", "status_command": "p1", "command": "x"},
              {"name": "b", "status_command": "p2", "command": "y"}]

    control.run_state_command(states, "b", shell_true=False, confirm_timeout=5.0)

    assert len(calls) == 1  # gave up right away, didn't keep polling for the full 5s budget


# ---------- _run_detached_detecting_quick_failure ----------
# Real subprocess spawns (true/false/sleep, via a real shell) rather
# than mocking spawn_detached/os.waitpid — process lifecycle timing is
# exactly what's under test here, so a real child process is the more
# trustworthy choice, not excessive. window_seconds is kept tiny so
# these stay fast.

def test_quick_failure_window_raises_with_captured_output():
    with pytest.raises(RuntimeError) as excinfo:
        control._run_detached_detecting_quick_failure(
            "echo boom 1>&2; exit 3", shell_true=True, window_seconds=0.3,
        )
    assert "boom" in str(excinfo.value)


def test_quick_failure_with_no_output_still_reports_the_exit_code():
    with pytest.raises(RuntimeError) as excinfo:
        control._run_detached_detecting_quick_failure("exit 1", shell_true=True, window_seconds=0.3)
    assert "exit 1" in str(excinfo.value) or "code 1" in str(excinfo.value)


def test_quick_success_does_not_raise():
    control._run_detached_detecting_quick_failure("true", shell_true=True, window_seconds=0.3)


def test_a_command_still_running_past_the_window_is_left_alone():
    # Idle Inhibit's own "on" command is exactly this shape (sleep
    # infinity) — must NOT be treated as a failure just because it
    # hasn't exited yet.
    control._run_detached_detecting_quick_failure("sleep 2", shell_true=True, window_seconds=0.1)


def test_output_temp_file_is_not_left_behind(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    control._run_detached_detecting_quick_failure("true", shell_true=True, window_seconds=0.2)
    assert list(tmp_path.iterdir()) == []


def test_survives_child_process_error_from_waitpid(monkeypatch):
    # Something else reaping the child first (a container's own init,
    # a subreaper misconfiguration) makes os.waitpid raise
    # ChildProcessError — must be treated as "can't tell", not crash
    # the whole toggle. os.getpid() is a real, deterministic way to
    # trigger this for real (a process is never its own child — same
    # trick test_pending_moves.py's own _check_quick_exit tests use),
    # not a mocked exception.
    monkeypatch.setattr(control, "spawn_detached", lambda *a, **k: os.getpid())
    control._run_detached_detecting_quick_failure("true", shell_true=True, window_seconds=0.2)
