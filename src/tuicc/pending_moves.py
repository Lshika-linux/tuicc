"""Matches a spawned process back to the window it produces, so it can be
moved to whichever region it was meant for.

There's no synchronous "this pid's window just appeared" signal from a WM
— a process starting and its window appearing in get_state() are two
separate events, arbitrarily far apart in time (a terminal opens in
~100ms, a heavy Electron app can take seconds). main.py snapshots the set
of window ids that exist right before spawning ("known_ids"), then each
frame checks whether any id in the current state wasn't in that snapshot
— that's the new window.

That alone only covers one spawn at a time; with more than one spawn in
flight (e.g. restoring several windows at once) two pending entries can
match the same new window without a shared "already claimed" set — see
CLAUDE/NOTES/design-decisions.md#pending-move-tiers.

PendingMovesQueue and the functions below it are the session-level layer
on top of resolve_pending_move's per-entry matching, replacing what
main.py's loop used to hold as loose locals. Same "pure function over an
explicit value" style as resize_mode.py/help_mode.py/launcher.py's
LauncherState: a dataclass is just the state, every function here takes
one and mutates it, main.py still owns *when* to call them.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from tuicc.actions import spawn_detached
from tuicc.model import Window

SPAWN_LOG_DIR = Path.home() / ".config" / "tuicc" / "logs"

# How long an entry keeps waiting on an exact pid match before process()
# downgrades it to app_id-tier matching instead (see process()'s loop).
# See CLAUDE/NOTES/design-decisions.md#pid-grace-seconds for why 6.0s.
PID_GRACE_SECONDS = 6.0
MOVE_TIMEOUT_SECONDS = 8.0
RESTORE_STAGGER_SECONDS = 0.3


def resolve_pending_move(entry: dict, current_windows: list[Window], claimed: set[str]) -> Window | None:
    """The window that satisfies entry, or None if nothing does yet.
    entry needs "known_ids" and may carry "pid"/"app_id" (either may be
    None). Matches in three exclusive tiers — pid, then app_id, then any
    remaining unclaimed new window — see
    CLAUDE/NOTES/design-decisions.md#pending-move-tiers for why they
    don't cascade. Doesn't mutate claimed itself — the caller adds the
    result's id only once it commits to the match.
    """
    new_windows = [w for w in current_windows if w.id not in entry["known_ids"] and w.id not in claimed]
    if not new_windows:
        return None

    # Each tier is exclusive, not a cascade — see
    # CLAUDE/NOTES/design-decisions.md#pending-move-tiers for why.
    expected_pid = entry.get("pid")
    if expected_pid is not None:
        for w in new_windows:
            if w.pid == expected_pid:
                return w
        return None

    expected_app_id = entry.get("app_id")
    if expected_app_id is not None:
        for w in new_windows:
            if w.app_id == expected_app_id:
                return w
        return None

    return new_windows[0]


@dataclass
class PendingMovesQueue:
    """entries mirrors the old pending_moves list (dicts, heterogeneous
    shape — see queue_restore_entry/queue_launcher_spawn below, they
    don't build identical key sets). claimed_ids mirrors
    claimed_window_ids, only cleared once entries is fully drained, not
    per-entry. last_restore_launch gates promote_restore_queue's
    staggering.
    """
    entries: list = field(default_factory=list)
    claimed_ids: set = field(default_factory=set)
    last_restore_launch: float = 0.0


def queue_restore_entry(queue: PendingMovesQueue, session_entry: dict, known_ids: set, pid, now: float) -> None:
    """Appends one entry for a session-restore spawn. Carries
    floating+rect when the saved window was floating (session.py's
    saved shape) — queue_launcher_spawn below never does, since the
    launcher has no saved geometry to restore.
    """
    entry = {
        "target_region": session_entry["target_region"],
        "known_ids": known_ids,
        "pid": pid,
        "app_id": session_entry["app_id"],
        "started_at": now,
        "floating": session_entry.get("floating", False),
    }
    if entry["floating"]:
        entry["rect"] = (
            session_entry["x"], session_entry["y"],
            session_entry["w"], session_entry["h"],
        )
    queue.entries.append(entry)


def queue_launcher_spawn(queue: PendingMovesQueue, target_region, known_ids: set, pid, app_id_hint, now: float) -> None:
    """Appends one entry for a launcher-confirmed spawn — never carries
    floating/rect, unlike queue_restore_entry's entries.
    """
    queue.entries.append({
        "target_region": target_region,
        "known_ids": known_ids,
        "pid": pid,
        "app_id": app_id_hint,
        "started_at": now,
    })


def promote_restore_queue(queue: PendingMovesQueue, provider, restore_queue: list, known_ids: set, now: float) -> None:
    """Pops one entry off restore_queue and spawns it, staggered by
    RESTORE_STAGGER_SECONDS. No-ops if restore_queue is empty (checked
    before the stagger-time comparison, so an empty queue never blocks
    a later real restore on a stale timestamp). Passes
    session_entry.get("env") through to spawn_detached() and a log_path
    under SPAWN_LOG_DIR — see
    CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash for why.
    """
    if not restore_queue:
        return
    if now - queue.last_restore_launch < RESTORE_STAGGER_SECONDS:
        return
    session_entry = restore_queue.pop(0)
    log_path = SPAWN_LOG_DIR / f"restore_{session_entry['app_id']}_{int(time.time())}.log"
    pid = spawn_detached(
        session_entry["cmdline"], shell_true=False, log_path=log_path,
        env=session_entry.get("env"),
    )
    # See Provider.no_focus_next_window()'s docstring — called right
    # after the pid is known, well before the restored window has had a
    # chance to map and steal focus/fullscreen from tuicc.
    provider.no_focus_next_window(pid)
    queue_restore_entry(queue, session_entry, known_ids, pid, now)
    queue.last_restore_launch = now


def _enrich_pids(queue: PendingMovesQueue, provider, current_windows: list[Window]) -> None:
    """Fills in .pid (in place) for windows get_state() left at None, via
    provider.resolve_pid() — on-demand, not part of the per-frame
    get_state() path, so this is the one place it's worth paying for.

    Scoped to windows no entry has seen yet and not already claimed
    (typically 0-1 windows, not every open window on the desktop) — see
    CLAUDE/NOTES/known-limitations.md#pid-enrichment-scope for why this
    scoping matters on providers without native pid support (i3). Safe
    to call unconditionally: provider.resolve_pid() defaults to a no-op
    returning None where it isn't needed (sway).
    """
    known_to_any_entry = set()
    for entry in queue.entries:
        known_to_any_entry |= entry.get("known_ids", set())
    for w in current_windows:
        if w.pid is None and w.id not in known_to_any_entry and w.id not in queue.claimed_ids:
            w.pid = provider.resolve_pid(w.id)


def process(
    queue: PendingMovesQueue, provider, current_windows: list[Window],
    dismissed: bool, now: float, fullscreen_only: bool = False,
    own_region_id: str | None = None,
) -> tuple[bool, list[str]]:
    """Resolves every entry in queue against current_windows: enriches
    pids, downgrades pid- to app_id-matching after PID_GRACE_SECONDS,
    moves+floats a matched window, then reclaims focus unless dismissed
    (must not un-hide a deliberately-dismissed tuicc). Entries past
    MOVE_TIMEOUT_SECONDS are dropped but still reclaim focus first.
    own_region_id decides whether to request force_relayout (see
    CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout). Returns
    (reclaimed_focus, resolved_target_regions) — see
    CLAUDE/NOTES/design-decisions.md#pending-moves-process-contract for
    the full contract and the bugs it fixes.
    """
    _enrich_pids(queue, provider, current_windows)
    reclaimed_focus = False
    resolved_target_regions = []
    still_pending = []
    for entry in queue.entries:
        if (entry.get("pid") is not None and entry.get("app_id") is not None
                and now - entry["started_at"] > PID_GRACE_SECONDS):
            entry["pid"] = None

        match = resolve_pending_move(entry, current_windows, queue.claimed_ids)
        if match is not None:
            queue.claimed_ids.add(match.id)
            provider.move_window_to_region(match.id, entry["target_region"])
            resolved_target_regions.append(entry["target_region"])
            if entry.get("floating"):
                provider.set_floating_geometry(match.id, entry["target_region"], entry["rect"])
            if not dismissed:
                force_relayout = own_region_id is not None and entry["target_region"] == own_region_id
                provider.focus_self(fullscreen=fullscreen_only, force_relayout=force_relayout)
                reclaimed_focus = True
        elif now - entry["started_at"] <= MOVE_TIMEOUT_SECONDS:
            still_pending.append(entry)
        elif not dismissed:
            # Giving up on this entry's match — see the docstring above
            # for why tuicc's own focus/fullscreen recovery must not
            # wait on that outcome.
            provider.focus_self(fullscreen=fullscreen_only)
            reclaimed_focus = True
    queue.entries = still_pending
    if not queue.entries:
        queue.claimed_ids.clear()
    return reclaimed_focus, resolved_target_regions