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
    """Probes each state's status_command in declaration order,
    returns the name of the first one whose exit code is 0. A state
    with no status_command (only ever valid on the last entry — see
    config.py's _build_control_toggles validation) is the implicit
    "whatever's left": only returned if every earlier state's probe
    came back false, a sound conclusion from exhaustive checking, not
    a guess.

    Raises RuntimeError if every state HAS a status_command and none
    of them matched — a real "this tool's actual output doesn't match
    any configured state" mismatch, surfaced via
    status_worker.StatusWorker's last_error like any other poll
    failure, not silently assumed to be the first or last one.
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
    """(current_index + 1) % len(states) — the toggle/cycle
    unification found while designing this contract: a plain flip and
    a multi-way cycle are the same advance rule, just a different
    len(states). Falls back to the first state if current_name is None
    or doesn't match any configured state (StatusWorker hasn't
    completed a poll yet, or its last poll errored) — a reasonable
    default for a user pressing Enter before the first poll lands, not
    a masking of a real error (modules/control.py's draw() shows that
    error state separately, on its own).
    """
    names = [state["name"] for state in states]
    if current_name not in names:
        return names[0]
    index = names.index(current_name)
    return names[(index + 1) % len(names)]


def _run_detached_detecting_quick_failure(
    command: str, shell_true: bool, window_seconds: float = QUICK_FAILURE_WINDOW_SECONDS
) -> None:
    """Spawns command detached (spawn_detached — same argv/shell
    resolution every other spawn in this codebase uses), then briefly
    waits up to window_seconds to see whether it's ALREADY exited.
    Found live building this module: a toggle's command can fail
    almost instantly (gammastep exiting right away for lack of a
    configured GeoClue2 provider, on a real test machine)
    with zero visible feedback — the toggle just silently stayed in
    its old state, StatusWorker's own action-error swallowing (fixed
    alongside this) meant even a real exception wouldn't have helped,
    since a failed shell command isn't a Python exception at all.

    A command still running past window_seconds is treated as a
    legitimate long-runner (the packaged Idle Inhibit example holds a
    systemd-inhibit lock via `sleep infinity`) or just a slow starter —
    either way, left alone; this function does not wait for it to
    actually finish, only checks whether it already has. Blocking here
    indefinitely would hang StatusWorker's action-dispatch loop for
    every future toggle press until that command exits.

    Raises RuntimeError (with the command's captured combined stdout+
    stderr, if any) when it exits nonzero within the window. Output is
    captured to an unlinked temp file, not a subprocess.PIPE — a PIPE
    nobody reads from risks blocking a legitimate long-runner the
    moment its output fills the OS pipe buffer; a plain file has no
    such limit, and unlinking it immediately is safe on Linux (the
    inode survives until the writing process's own fd closes), so
    nothing is left behind on disk even for a still-running process.
    """
    with tempfile.NamedTemporaryFile(delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
        pid = spawn_detached(command, shell_true, log_path=output_path)
        deadline = time.monotonic() + window_seconds
        while time.monotonic() < deadline:
            finished_pid, status = os.waitpid(pid, os.WNOHANG)
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
    find_current_state() until it actually reports target_name (or
    confirm_timeout elapses).

    This function's return is what clears StatusWorker's `pending`
    flag for this toggle (see status_worker.py's _run() — pending is
    discarded right when the requested action() call returns) — found
    live, mattering in practice: without this wait, pending cleared
    the instant the command was merely SPAWNED, well before its real
    effect was necessarily visible via status_command, leaving the
    blink stop well ahead of the row actually showing the new state
    (and, since main.py's redraw cadence is itself tied to
    has_pending(), the whole module visibly stalling in between —
    reported as "the module feels slow"). Bounded by confirm_timeout so
    a toggle that genuinely never converges (a command that silently
    no-ops, say) doesn't block every future toggle press behind it
    forever — StatusWorker's action queue is processed sequentially,
    one at a time.
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
