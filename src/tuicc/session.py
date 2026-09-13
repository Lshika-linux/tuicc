"""Session save/restore: capture the exact command each currently-open
window was started with, so a later restore can relaunch the same set
of apps onto the same regions.

    WMState -> capture_session() -> [{app_id, cmdline, target_region,
    floating, ...}] -> save_session() -> plain TOML file, user-editable
                                          like everything else in tuicc

Matching a relaunched process back to the window it produces already
exists in pending_moves.py — this module only turns live windows into
a saved record and back, it doesn't launch or place anything itself.

Floating geometry is saved normalized (0..1, same space as Window.rect)
rather than in absolute pixels — a saved session stays meaningful if
restored on a different-resolution screen later, matching the reason
tuicc normalizes rect everywhere else (see providers/base.py).
"""

import shlex
import tomllib
import tomli_w
from pathlib import Path

from tuicc.model import WMState, Window

SESSIONS_DIR = Path.home() / ".config" / "tuicc" / "sessions"


def _parse_cmdline(raw: bytes) -> list[str]:
    """Pure: /proc/<pid>/cmdline's raw null-byte-separated bytes -> argv.
    Testable without touching a real /proc file.
    """
    return [part.decode() for part in raw.split(b"\x00") if part]


def normalize_saved_cmdline(cmdline: list[str]) -> list[str]:
    """A saved session entry's cmdline, as it should actually be handed
    to spawn_detached() — almost always a no-op, since _parse_cmdline()
    already produces a clean argv list. The one exception, found live
    restoring a saved Obsidian entry: some Electron/Chromium versions
    rewrite their own argv memory on Linux to show a friendlier name in
    `ps`/`top` (a well-known setproctitle-style trick), and do it by
    overwriting the null-byte argv separators with plain spaces — so
    what was really invoked as ["electron", "/path/app.asar"] shows up
    in /proc/<pid>/cmdline, by the time tuicc reads it, as a SINGLE
    argument: "electron /path/app.asar". Handed to spawn_detached() as a
    one-element list, that's an argv of one nonexistent path (the space
    is part of the "filename"), not two real arguments — a plain ENOENT,
    not anything specific to Obsidian.

    Only re-split when there's exactly one argv element AND it contains
    whitespace — every multi-element cmdline (the overwhelming majority)
    is returned untouched, so this can't rewrite a normal, correctly-
    captured launch command. shlex.split() raising ValueError (a stray
    unbalanced quote — nothing here guarantees the collapsed string was
    ever valid shell syntax to begin with) falls back to the original
    single element unchanged, so a spawn that would have failed with
    today's plain ENOENT still fails exactly that way, not with an
    uncaught exception.

    Known, accepted cost of the heuristic itself: a genuinely single-
    argument command whose one argument is an executable path that
    contains a literal space (rare on Linux, not impossible) gets
    incorrectly split too. Narrower than applying this to every cmdline
    shape, and the failure mode it fixes is far more common in practice
    — see CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash.

    Called from two places, deliberately, not just one:
    capture_window() is the authoritative point — a freshly-saved
    session.toml should already show the real, spawn-ready argv, not a
    raw /proc capture that only becomes correct via a later invisible
    step (session files are plain, user-editable TOML; what's on disk
    ought to be honest about what will actually run). promote_restore_queue()
    also calls it, purely as a backward-compatible safety net for a
    session.toml saved before this fix existed (or hand-edited into the
    collapsed shape) — idempotent on an already-correct multi-element
    cmdline, so calling it twice on a freshly-saved entry is a no-op,
    not a double-transform.
    """
    if len(cmdline) != 1 or not any(ch.isspace() for ch in cmdline[0]):
        return cmdline
    try:
        return shlex.split(cmdline[0])
    except ValueError:
        return cmdline


def read_cmdline(pid: int) -> list[str] | None:
    """The exact argv a running process was started with. None if the
    process is already gone or /proc is unreadable — a caller should
    treat this the same as "can't capture this window", not an error.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if not raw:
        return None
    return _parse_cmdline(raw)


# Skipped when capturing environ, case-insensitive substring match —
# best-effort, not exhaustive, but session files are plain, unencrypted
# TOML on disk (same as everything else tuicc saves), and an env var
# whose name suggests a credential is exactly the kind of thing that
# shouldn't end up there just because it happened to be set when a
# window got captured.
_SENSITIVE_ENV_KEY_MARKERS = ("token", "key", "secret", "password", "passwd", "auth", "credential")


def _parse_environ(raw: bytes) -> dict[str, str]:
    """Pure: /proc/<pid>/environ's raw null-byte-separated KEY=VALUE
    bytes -> dict, dropping anything matching _SENSITIVE_ENV_KEY_MARKERS.
    Testable without touching a real /proc file, same reasoning as
    _parse_cmdline.
    """
    env = {}
    for part in raw.split(b"\x00"):
        if not part:
            continue
        key, sep, value = part.decode(errors="replace").partition("=")
        if not sep:
            continue
        if any(marker in key.lower() for marker in _SENSITIVE_ENV_KEY_MARKERS):
            continue
        env[key] = value
    return env


def read_environ(pid: int) -> dict[str, str] | None:
    """The environment a running process was started with (minus
    anything that looks like a credential — see
    _SENSITIVE_ENV_KEY_MARKERS). None if the process is already gone or
    /proc is unreadable — same "can't capture this" contract as
    read_cmdline(), but unlike read_cmdline() this one being
    uncapturable doesn't sink the whole window: env is a restore-
    reliability enhancement, not a hard requirement (a plain relaunch
    without it works for most apps — see
    CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash for a case
    where it doesn't).
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if not raw:
        return None
    return _parse_environ(raw)


def capture_window(window: Window, region_id: str, provider) -> dict | None:
    """One window's saved-session record, or None if we can't find
    enough to relaunch it later (no pid from get_state()/resolve_pid(),
    or the process is already gone). "env" is included when
    read_environ() succeeds, omitted (not entry=None) when it doesn't
    — see CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash for
    why it matters.

    cmdline goes through normalize_saved_cmdline() before it's saved —
    deliberately here, not only at restore time: a saved session.toml is
    "plain TOML, user-editable like everything else in tuicc" (this
    module's own docstring), so what's on disk should already be the
    real, correct, spawn-ready argv a person could read or hand-edit,
    not a raw /proc capture that only becomes right via an invisible
    step at restore. See normalize_saved_cmdline()'s own docstring for
    why the fix is applied at BOTH points, not just this one.
    """
    pid = window.pid if window.pid is not None else provider.resolve_pid(window.id)
    if pid is None:
        return None

    cmdline = read_cmdline(pid)
    if cmdline is None:
        return None
    cmdline = normalize_saved_cmdline(cmdline)

    entry = {
        "app_id": window.app_id,
        "cmdline": cmdline,
        "target_region": region_id,
        "floating": window.floating,
    }
    env = read_environ(pid)
    if env is not None:
        entry["env"] = env
    if window.floating:
        x, y, w, h = window.rect
        entry["x"] = x
        entry["y"] = y
        entry["w"] = w
        entry["h"] = h
    return entry


def capture_session(state: WMState, provider) -> list[dict]:
    """Every currently-open window across every region, as save-able
    entries. Windows that can't be captured (see capture_window) are
    silently skipped, not an error — a partial session missing one
    unreachable window is still useful; refusing to save the other 9
    over 1 failure wouldn't be.
    """
    entries = []
    for region in state.regions:
        for window in region.windows:
            entry = capture_window(window, region.id, provider)
            if entry is not None:
                entries.append(entry)
    return entries


def save_session(entries: list[dict], path: Path) -> None:
    """Write entries as [[window]] blocks to path, atomically — a crash
    or Ctrl-C mid-write must never corrupt an existing save (same
    reasoning tileroot's dump -o uses for the same problem).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "wb") as f:
        tomli_w.dump({"window": entries}, f)
    tmp_path.replace(path)


def load_session(path: Path) -> list[dict]:
    """Read a previously saved session back into the same shape
    capture_session() produces."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("window", [])