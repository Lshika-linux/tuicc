"""Matches a spawned process back to the window it produces, so it can be
moved to whichever region it was meant for.

There's no synchronous "this pid's window just appeared" signal from a WM
— a process starting and its window appearing in get_state() are two
separate events, arbitrarily far apart in time (a terminal opens in
~100ms, a heavy Electron app can take seconds). main.py snapshots the set
of window ids that exist right before spawning ("known_ids"), then each
frame checks whether any id in the current state wasn't in that snapshot
— that's the new window.

That alone is enough for one spawn at a time. It breaks down the moment
more than one spawn is in flight together (e.g. restoring several windows
at once): with no shared "already used" tracking, two pending entries can
both match the same new window, or the wrong window can be assigned to
the wrong target — verified live against a real WM by testing a similar
tool's matcher (github.com/Hinikaa/tileroot) which has exactly this gap.

PendingMovesQueue and the functions below it are the session-level layer
on top of resolve_pending_move's per-entry matching — what main.py's loop
used to hold as two loose locals (pending_moves/claimed_window_ids) plus
a bare float (last_restore_launch) and three module-level constants.
Same "pure function over an explicit value" style as resize_mode.py/
help_mode.py/launcher.py's LauncherState: a dataclass is just the state,
every function here takes one and mutates it, main.py still owns *when*
to call them (and still owns `dismissed`, threaded in as an explicit
param — see process()'s docstring).
"""

from dataclasses import dataclass, field

from tuicc.actions import spawn_detached
from tuicc.model import Window

PID_GRACE_SECONDS = 1.5
MOVE_TIMEOUT_SECONDS = 8.0
RESTORE_STAGGER_SECONDS = 0.3


def resolve_pending_move(entry: dict, current_windows: list[Window], claimed: set[str]) -> Window | None:
    """The window that satisfies entry, or None if nothing does yet.

    entry needs "known_ids" (the snapshot taken before spawning) and may
    optionally carry "pid" and/or "app_id" — either can be None when
    unknown (a plain launcher spawn has no pre-known app_id; a provider
    without pid support, i3 today, never has one).

    Priority, each tier only considering windows not already in claimed:
      1. exact pid match — unambiguous, but only ever available when the
         provider exposes Window.pid (sway) and the process wasn't
         launched via a shell (see spawn_detached's shell_true note).
      2. app_id match — distinguishes "the app I'm expecting" from an
         unrelated new window, but not from a second simultaneous
         instance of the exact same app_id (harmless if both instances
         are otherwise identical — see the design discussion this
         shipped with for why that's an accepted tradeoff rather than a
         bug). Callers processing multiple pending entries in order,
         each claiming one match before the next entry looks, is what
         keeps same-app_id entries from colliding — this function only
         picks the first unclaimed match available *to it*, it has no
         notion of which window actually appeared first in real time.
      3. any remaining unclaimed new window — last resort, same
         behavior as before this module existed.

    Doesn't mutate claimed itself — the caller adds the result's id once
    it actually commits to the match (calls move_window_to_region), so a
    per-tier scan here never has a side effect to undo if the caller
    decides not to use the result.
    """
    new_windows = [w for w in current_windows if w.id not in entry["known_ids"] and w.id not in claimed]
    if not new_windows:
        return None

    # Each tier is exclusive, not a cascade: an entry that expects a
    # specific pid/app_id must wait for exactly that (or eventually time
    # out in the caller), never settle for a weaker signal just because
    # its own match hasn't appeared on *this* tick yet. Falling through
    # early looks fine right up until two entries share an app_id (e.g.
    # two `kitty` launches) and one's window is simply slower to appear
    # — a premature fallback then hands it the wrong entry's window,
    # verified live: this exact bug reassigned 3 of 10 windows to the
    # wrong workspace in a real burst-launch test before this fix.
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


def promote_restore_queue(queue: PendingMovesQueue, restore_queue: list, known_ids: set, now: float) -> None:
    """Pops one entry off restore_queue (session.py's load queue) and
    spawns it, staggered by RESTORE_STAGGER_SECONDS so a multi-window
    session restore doesn't fire every process in the same frame.
    No-ops if restore_queue is empty — checked BEFORE the stagger-time
    comparison, so an empty queue never touches last_restore_launch and
    a later real restore isn't held back by a stale timestamp.
    """
    if not restore_queue:
        return
    if now - queue.last_restore_launch < RESTORE_STAGGER_SECONDS:
        return
    session_entry = restore_queue.pop(0)
    pid = spawn_detached(session_entry["cmdline"], shell_true=False)
    queue_restore_entry(queue, session_entry, known_ids, pid, now)
    queue.last_restore_launch = now


def _enrich_pids(queue: PendingMovesQueue, provider, current_windows: list[Window]) -> None:
    """Fills in .pid (in place) for windows get_state() left at None,
    via provider.resolve_pid() — on-demand, not part of the per-frame
    get_state() path, so this is the one place it's worth paying for.

    Only for windows no entry has seen yet (not in ANY entry's
    known_ids) and not already claimed — the actual candidate set for
    a NEW spawn, typically 0-1 windows, not every open window on the
    desktop. Scoped this way because resolve_pending_move's pid tier
    is otherwise structurally dead on a provider whose get_state()
    never populates pid at all (i3 today — its GET_TREE has no pid
    field, unlike sway's): every pending entry would burn
    PID_GRACE_SECONDS, downgrade to the app_id tier, and — if the
    spawned app's actual runtime app_id doesn't match what its
    .desktop entry hinted (not rare for Python/Electron apps launched
    via a bare interpreter, which often report the interpreter's own
    class instead of the app's) — never resolve at all, silently
    dropped after MOVE_TIMEOUT_SECONDS. Found live: a spawn timing out
    this way left its window sitting wherever the WM naturally opened
    it — tuicc's own workspace, in the reproducing case — with no
    resolved match to move it, which is also what triggered the
    unrelated-looking symptom of tuicc losing fullscreen (a new tiled
    window landing on its workspace).

    provider.resolve_pid() defaults to a no-op returning None for any
    provider that doesn't override it (sway doesn't need to — its
    windows already carry a real pid from get_state()), so calling
    this unconditionally is safe everywhere, just a no-op where it
    isn't needed.
    """
    known_to_any_entry = set()
    for entry in queue.entries:
        known_to_any_entry |= entry.get("known_ids", set())
    for w in current_windows:
        if w.pid is None and w.id not in known_to_any_entry and w.id not in queue.claimed_ids:
            w.pid = provider.resolve_pid(w.id)


def process(queue: PendingMovesQueue, provider, current_windows: list[Window], dismissed: bool, now: float) -> bool:
    """Resolves every entry in queue against current_windows: enriches
    still-unknown pids via provider.resolve_pid() first (see
    _enrich_pids — the fix for providers, i3 today, whose get_state()
    never supplies one), downgrades an entry from pid- to app_id-
    matching after PID_GRACE_SECONDS if that still didn't produce one
    (a next-call handoff, not a same-tick fallback — see
    resolve_pending_move's own docstring for why that distinction
    matters), moves + optionally floats a matched window, then reclaims
    focus for tuicc unless dismissed is True — focusing a scratchpadded
    tuicc window un-hides it on sway/i3, so this must not fire while
    tuicc was deliberately dismissed after the spawn but before it
    resolved (see main.py's own `dismissed` comment for the full story,
    including its one known, narrow limitation). Entries that neither
    match nor are within MOVE_TIMEOUT_SECONDS are silently dropped —
    give-up-by-omission, not an explicit failure state.

    Returns whether focus_self() was called this round — a real WM-
    focus transition, but a self-inflicted one, not the user having
    switched to a different real context. main.py's loop needs to
    know this to avoid its own "real WM-focus transition" detector
    (see its comment) misreading tuicc reclaiming its own focus as the
    user having gone elsewhere, which would otherwise silently reset
    selected_id/focus_id mid-launcher-session — see the regression
    test for the concrete bug this caused (a spawn's target silently
    switching workspace because an earlier spawn's focus_self() call
    happened to land between selecting a workspace and confirming).
    """
    _enrich_pids(queue, provider, current_windows)
    reclaimed_focus = False
    still_pending = []
    for entry in queue.entries:
        if (entry.get("pid") is not None and entry.get("app_id") is not None
                and now - entry["started_at"] > PID_GRACE_SECONDS):
            entry["pid"] = None

        match = resolve_pending_move(entry, current_windows, queue.claimed_ids)
        if match is not None:
            queue.claimed_ids.add(match.id)
            provider.move_window_to_region(match.id, entry["target_region"])
            if entry.get("floating"):
                provider.set_floating_geometry(match.id, entry["target_region"], entry["rect"])
            if not dismissed:
                provider.focus_self()
                reclaimed_focus = True
        elif now - entry["started_at"] <= MOVE_TIMEOUT_SECONDS:
            still_pending.append(entry)
    queue.entries = still_pending
    if not queue.entries:
        queue.claimed_ids.clear()
    return reclaimed_focus