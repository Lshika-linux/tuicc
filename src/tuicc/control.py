"""Control module's toggle backend — VISION.md's R5. Unlike audio/
connectivity, there's no registry of alternative implementations here:
every [[control.toggle]] entry is an arbitrary user-configured shell
command (see config.py's _build_control_toggles), so "the backend" is
just running those commands plus the one piece of real logic worth
testing as a pure function — matching a probed state against the
configured list, and advancing through it. A plain on/off switch and a
multi-way cycle (e.g. Performance Mode's power-saver/balanced/
performance) are the exact same shape here, found while designing the
config contract with the user: len(states) == 2 is just the toggle
case of len(states) == N, and advancing is (index + 1) % len(states)
regardless of N.

No internal try/except in probe_state — same reasoning as every other
R3/R5 backend (audio/, brightness.py, the fixed connectivity/iwd.py
and bluez.py): a real failure (missing binary, timeout) must propagate
to status_worker.StatusWorker's poll wrapper, not vanish silently.
"""

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from tuicc.actions import spawn_detached

# How long run_state_command briefly waits to see whether a just-spawned
# command has ALREADY exited, before giving up and assuming it's a
# legitimate long-runner — see _run_detached_detecting_quick_failure's
# own docstring for why this exists at all.
QUICK_FAILURE_WINDOW_SECONDS = 0.3

# How long run_state_command additionally polls for the target state to
# actually be confirmed via status_command, after the quick-failure
# window above already passed — see run_state_command's own docstring
# for why this exists (StatusWorker's pending/blink indicator is tied
# directly to how long this whole function takes to return).
CONFIRM_TIMEOUT_SECONDS = 2.0


def probe_state(status_command: str, shell_true: bool) -> bool:
    """Runs status_command, True if it exited 0 ("currently in this
    state"). capture_output=True so a probe's own stdout/stderr never
    lands on tuicc's curses screen, same reasoning as every subprocess
    call elsewhere in this codebase.
    """
    cmd = status_command if shell_true else shlex.split(status_command)
    result = subprocess.run(cmd, shell=shell_true, capture_output=True, text=True, timeout=5)
    return result.returncode == 0


def find_current_state(states: list[dict], shell_true: bool) -> str:
    """Probes each state's status_command in declaration order, returns
    the name of the first one whose exit code is 0. A state with no
    status_command (only valid on the last entry) is the implicit
    "whatever's left" — returned only if every earlier probe came back
    false. Raises RuntimeError if every state has a status_command and
    none matched, surfaced via StatusWorker's last_error like any other
    poll failure rather than assumed.
    """
    unprobed_name = None
    for state in states:
        if state["status_command"] is None:
            unprobed_name = state["name"]
            continue
        if probe_state(state["status_command"], shell_true):
            return state["name"]
    if unprobed_name is not None:
        return unprobed_name
    raise RuntimeError("status_command output matched none of the configured states")


def next_state_name(states: list[dict], current_name: str | None) -> str:
    """(current_index + 1) % len(states) — a plain flip and a
    multi-way cycle are the same advance rule, just a different
    len(states). Falls back to the first state if current_name is None
    or unrecognized (StatusWorker hasn't polled yet, or last errored)
    — a reasonable default, not error-masking: modules/control.py's
    draw() shows that error state separately.
    """
    names = [state["name"] for state in states]
    if current_name not in names:
        return names[0]
    index = names.index(current_name)
    return names[(index + 1) % len(names)]


def _run_detached_detecting_quick_failure(
    command: str, shell_true: bool, window_seconds: float = QUICK_FAILURE_WINDOW_SECONDS
) -> None:
    """Spawns command detached, then briefly waits up to window_seconds
    to see whether it's already exited — catches a command that fails
    almost instantly (see CLAUDE/VISION.md's R3 section for the
    gammastep/Night Light example this exists for; a failed shell
    command isn't a Python exception, so nothing else would catch it).
    A command still running past window_seconds is left alone as a
    legitimate long-runner or slow starter — never waited on further.
    Raises RuntimeError (with captured stdout+stderr) on a nonzero exit
    within the window. Output goes to an unlinked temp file, not
    subprocess.PIPE, so a long-runner can't block on a full pipe.

    os.waitpid() is expected to always find this exact child — this
    process is its real, direct parent — but ChildProcessError (no
    such child) is still caught rather than left to crash the whole
    toggle: something else reaping it first (a container's own init
    aggressively reaping children, a subreaper misconfiguration) isn't
    this function's job to prevent, only to survive. Same tolerance
    pending_moves.py's own _check_quick_exit() already applies to the
    identical os.waitpid(pid, os.WNOHANG) pattern there — treated as
    "can't tell", same as the command still running past the window.
    """
    with tempfile.NamedTemporaryFile(delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
        pid = spawn_detached(command, shell_true, log_path=output_path)
        deadline = time.monotonic() + window_seconds
        while time.monotonic() < deadline:
            try:
                finished_pid, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if finished_pid == pid:
                returncode = os.waitstatus_to_exitcode(status)
                if returncode != 0:
                    output_text = output_path.read_text(errors="replace").strip()
                    raise RuntimeError(output_text or f"{command!r} exited with code {returncode}")
                return
            time.sleep(0.02)
        # Still running past the window — assumed a legitimate
        # long-runner (or just slow), not this function's job to watch
        # any further.
    finally:
        output_path.unlink(missing_ok=True)


def run_state_command(
    states: list[dict], target_name: str, shell_true: bool, confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS
) -> None:
    """Spawns target_name's command detached, briefly checking for a
    fast failure (_run_detached_detecting_quick_failure), then polls
    find_current_state() until it reports target_name (or
    confirm_timeout elapses). This function's return is what clears
    StatusWorker's `pending` flag (see status_worker.py's _run()) —
    without this wait, pending would clear the instant the command was
    merely spawned, well before its real effect was visible, stalling
    the whole module's redraw cadence in between (has_pending()-tied).
    Bounded by confirm_timeout so a toggle that never converges doesn't
    block the sequential action queue forever.
    """
    for state in states:
        if state["name"] != target_name:
            continue
        _run_detached_detecting_quick_failure(state["command"], shell_true)
        deadline = time.monotonic() + confirm_timeout
        while time.monotonic() < deadline:
            try:
                if find_current_state(states, shell_true) == target_name:
                    return
            except RuntimeError:
                return  # status matches no configured state at all - not this function's job to keep retrying
            time.sleep(0.1)
        return  # timed out without confirmation - not necessarily a failure, just stop waiting
    raise ValueError(f"no state named {target_name!r} in {[s['name'] for s in states]}")
