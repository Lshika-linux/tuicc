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
    enough to relaunch it later — no pid available from either
    get_state() directly (sway) or the provider's resolve_pid()
    fallback (i3), or the process is already gone by the time we
    check its cmdline.

    "env" is included when read_environ() succeeds, omitted (not
    entry=None — cmdline alone is still enough to attempt a relaunch)
    when it doesn't. Matters because /proc/<pid>/cmdline only ever
    captures argv *after* any launcher wrapper script has already
    exec'd into the real process — on NixOS, GUI apps are commonly
    packaged as exactly such a wrapper (setting up env before exec'ing
    into the raw binary), so the plain cmdline alone can be missing
    exactly the context a relaunch needs. Found live: a saved Obsidian
    entry's cmdline relaunched bare fails with `Cannot find module
    'electron'` — see spawn_detached()'s docstring for the fix this
    enables on the restore side.
    """
    pid = window.pid if window.pid is not None else provider.resolve_pid(window.id)
    if pid is None:
        return None

    cmdline = read_cmdline(pid)
    if cmdline is None:
        return None

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