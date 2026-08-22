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

The captured pid isn't always the window's real owning pid either — an
updater/wrapper that forks a genuinely different process for the real
app (rather than exec-replacing itself) leaves the captured pid
correct but useless (CLAUDE/NOTES/known-limitations.md
#fork-exec-pid-mismatch; live-confirmed with Discord). _grow_known_pids()
tracks the captured pid's WHOLE observed descendant tree (via
procmon.py's own subtree-walk, built for R6's per-window process
aggregation) every frame, so a forked replacement matches the instant
its window maps — no waiting on PID_GRACE_SECONDS/MOVE_TIMEOUT_SECONDS,
and no dependence on app_id also happening to match.

PendingMovesQueue and the functions below it are the session-level layer
on top of resolve_pending_move's per-entry matching, replacing what
main.py's loop used to hold as loose locals. Same "pure function over an
explicit value" style as resize_mode.py/help_mode.py/launcher.py's
LauncherState: a dataclass is just the state, every function here takes
one and mutates it, main.py still owns *when* to call them.
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from tuicc.actions import spawn_detached
from tuicc.model import Window
from tuicc.procmon import scan_all_processes, build_children_map, subtree_pids

SPAWN_LOG_DIR = Path.home() / ".config" / "tuicc" / "logs"

# How long an entry keeps waiting on an exact pid match before process()
# downgrades it to app_id-tier matching instead (see process()'s loop).
# See CLAUDE/NOTES/design-decisions.md#pid-grace-seconds for why 6.0s.
PID_GRACE_SECONDS = 6.0
MOVE_TIMEOUT_SECONDS = 8.0
RESTORE_STAGGER_SECONDS = 0.3
# How long an entry that has ALREADY matched at least one window keeps
# watching for a FURTHER one from the same spawn before it's considered
# done — unconditional, applied after every match, not just ones that
# already look like an updater-then-real-app pattern (fork/exec, see
# process()'s own docstring for why gating this on known_pids already
# showing a descendant doesn't work: the descendant often doesn't exist
# yet at match time). The accepted cost is a few extra seconds of
# ~50ms-cadence polling after every launcher spawn, not just the ones
# that turn out to need it.
SETTLE_SECONDS = 6.0


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
        # known_pids (grown by process() every frame — see its own
        # docstring) is the captured pid's WHOLE observed descendant
        # tree, not just the exact pid — an updater that forks/execs a
        # different real process (CLAUDE/NOTES/known-limitations.md
        # #fork-exec-pid-mismatch) still matches via its child, the
        # instant that child's window maps, no PID_GRACE_SECONDS/
        # MOVE_TIMEOUT_SECONDS wait needed. Falls back to just
        # {expected_pid} if process() hasn't grown it yet (or a caller
        # constructs an entry by hand, e.g. in tests).
        known_pids = entry.get("known_pids") or {expected_pid}
        for w in new_windows:
            if w.pid in known_pids:
                return w
        return None

    expected_app_id = entry.get("app_id")
    if expected_app_id is not None:
        # Case-insensitive: a .desktop's StartupWMClass= and a window's
        # real runtime app_id commonly differ only in case — confirmed
        # live with VS Code (StartupWMClass=Code, real app_id=code).
        # Not the separate, still-open fork/exec pid-mismatch class
        # (CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch,
        # where the two strings are genuinely unrelated) — just a
        # casing convention gap between how a .desktop file and a
        # window's own runtime identity happen to spell the same name.
        expected_app_id_lower = expected_app_id.lower()
        for w in new_windows:
            if w.app_id.lower() == expected_app_id_lower:
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


def queue_restore_entry(
    queue: PendingMovesQueue, session_entry: dict, known_ids: set, pid, now: float,
    log_path: Path | None = None,
) -> None:
    """Appends one entry for a session-restore spawn. Carries
    floating+rect when the saved window was floating (session.py's
    saved shape) — queue_launcher_spawn below never does, since the
    launcher has no saved geometry to restore. log_path mirrors
    queue_launcher_spawn's own param, same reason — promote_restore_queue
    below already captures spawn_detached()'s output for a different
    reason (CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash);
    threading the same path through here means a fast nonzero-exit
    failure toast can reference it too, not just launcher spawns.
    root_pid/known_pids: see resolve_pending_move()'s own docstring —
    root_pid is the immutable seed process() walks the descendant tree
    from every frame (independent of "pid" above, which DOES get
    cleared on a tier downgrade); known_pids is what actually gets
    matched against, grown (never shrunk) as real descendants are
    observed.
    """
    entry = {
        "target_region": session_entry["target_region"],
        "known_ids": known_ids,
        "pid": pid,
        "root_pid": pid,
        "known_pids": {pid} if pid is not None else set(),
        "app_id": session_entry["app_id"],
        "started_at": now,
        "floating": session_entry.get("floating", False),
        "log_path": log_path,
    }
    if entry["floating"]:
        entry["rect"] = (
            session_entry["x"], session_entry["y"],
            session_entry["w"], session_entry["h"],
        )
    queue.entries.append(entry)


def queue_launcher_spawn(
    queue: PendingMovesQueue, target_region, known_ids: set, pid, app_id_hint, now: float,
    log_path: Path | None = None,
) -> None:
    """Appends one entry for a launcher-confirmed spawn — never carries
    floating/rect, unlike queue_restore_entry's entries. log_path (when
    the caller captured spawn_detached()'s output) is read back by
    _quick_exit_failure_message() on a fast nonzero-exit give-up, so the
    user's failure toast can point at real captured stderr instead of
    just an exit code. root_pid/known_pids — see queue_restore_entry's
    own docstring, same reasoning.
    """
    queue.entries.append({
        "target_region": target_region,
        "known_ids": known_ids,
        "pid": pid,
        "root_pid": pid,
        "known_pids": {pid} if pid is not None else set(),
        "app_id": app_id_hint,
        "started_at": now,
        "log_path": log_path,
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
    queue_restore_entry(queue, session_entry, known_ids, pid, now, log_path)
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


def _grow_known_pids(queue: PendingMovesQueue) -> None:
    """Extends every entry's known_pids with whatever real descendants
    root_pid has spawned since the last frame — see
    resolve_pending_move()'s own docstring for what known_pids is used
    for, and CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch
    for the live bug (Discord: updater exits, real app relaunches as a
    genuinely different process) this exists to catch fast instead of
    falling through to app_id-tier/timeout.

    ONE /proc scan for the whole queue, not one per entry — same
    "aggregating N things costs one scan, not N" reasoning
    scan_all_processes()'s own docstring gives for procmon.py's per-
    window subtree walks. Accumulates (union), never replaces:
    once a real descendant is observed under root_pid, it stays a
    known match forever, even after root_pid itself exits and any
    later /proc snapshot shows that pid reparented away (to init/a
    subreaper) and no longer nested under root_pid at all — the
    window for observing the TRUE parent-child link, while it's still
    intact, is real but narrow (this queue is polled every ~50ms while
    anything's pending; that's normally plenty of ticks before even a
    fast updater exits, but isn't a hard guarantee for a
    fork-then-immediately-exit under a millisecond, an accepted,
    probabilistic gap in the same spirit as mark_self()'s own
    focus-race fallback).
    """
    if not queue.entries:
        return
    children_map = build_children_map(scan_all_processes())
    for entry in queue.entries:
        root_pid = entry.get("root_pid")
        if root_pid is None:
            continue
        entry.setdefault("known_pids", {root_pid}).update(subtree_pids(root_pid, children_map))


@dataclass
class PendingMovesResult:
    """process()'s return value — see
    CLAUDE/NOTES/design-decisions.md#pending-moves-process-contract for
    the full contract. Promoted from a (reclaimed_focus,
    resolved_target_regions) 2-tuple once failure_messages was added —
    same "value outgrew 2 fields, make it a dataclass" convention as
    frame_update.FrameResult/resize_mode.EditKeyResult. failure_messages
    is populated on a quick nonzero-exit give-up or a MOVE_TIMEOUT_SECONDS
    give-up; never on a real match or while an entry is still pending.
    """
    reclaimed_focus: bool
    resolved_target_regions: list
    failure_messages: list = field(default_factory=list)


def _check_quick_exit(entry: dict) -> int | None:
    """Non-blocking check of whether entry's spawned pid has already
    exited, via os.waitpid(pid, os.WNOHANG) — the same primitive
    control.py's _run_detached_detecting_quick_failure() uses, but
    called once per frame here instead of in a bounded spin-loop, since
    process() itself already runs every frame. Returns the cached exit
    code once known (including 0 — a valid, real result, not "unknown"),
    None while still running or when entry has no pid to check. Caches
    onto entry["exit_code"] so a reaped pid's status is never asked for
    twice (os.waitpid raises ChildProcessError the second time).
    """
    if entry.get("pid") is None:
        return None
    if "exit_code" in entry:
        return entry["exit_code"]
    try:
        finished_pid, status = os.waitpid(entry["pid"], os.WNOHANG)
    except ChildProcessError:
        # Already reaped elsewhere, or not actually our child — can't
        # tell what happened; stop asking, fall back to the
        # PID_GRACE_SECONDS timer like before this check existed.
        entry["exit_code"] = None
        return None
    if finished_pid == 0:
        return None  # still running
    entry["exit_code"] = os.waitstatus_to_exitcode(status)
    return entry["exit_code"]


def _quick_exit_failure_message(entry: dict, exit_code: int) -> str:
    """One line, safe for draw_status_line's single-line/term_width-
    clipped toast — full captured output (if any) stays in the on-disk
    log file, referenced by name only, never embedded here.
    """
    label = entry.get("app_id") or "Command"
    log_path = entry.get("log_path")
    if log_path is not None:
        return f"{label} exited (code {exit_code}) — see {log_path.name}"
    return f"{label} exited (code {exit_code})"


def _timeout_failure_message(entry: dict) -> str:
    """Distinct wording from _quick_exit_failure_message: no exit code
    to report here — the process may still be running (e.g. the
    fork/exec pid-mismatch class in
    CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch, which this
    module's quick-exit check can't see since that pid never exits).
    """
    label = entry.get("app_id") or "Command"
    return f"{label} never opened a window (timed out)"


def process(
    queue: PendingMovesQueue, provider, current_windows: list[Window],
    dismissed: bool, now: float, fullscreen_only: bool = False,
    own_region_id: str | None = None,
) -> PendingMovesResult:
    """Resolves every entry in queue against current_windows: enriches
    pids, grows each entry's known_pids with any real descendants its
    root_pid has spawned (see _grow_known_pids), downgrades pid- to
    app_id-matching either immediately (on a confirmed quick clean exit
    — see _check_quick_exit — but only when no real descendant was ever
    observed, see that block's own comment) or after PID_GRACE_SECONDS
    as a fallback for pids that never exit at all, moves+floats a
    matched window, then reclaims focus unless dismissed (must not
    un-hide a deliberately-dismissed tuicc).

    A match does NOT finalize an entry: one spawn can legitimately
    produce more than one window over time — an updater whose own
    window maps and matches first, then backgrounds a genuinely
    different process for the real app instead of exec-replacing
    itself (CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch,
    live-confirmed with Discord). Every entry, matched or not yet,
    stays pending for SETTLE_SECONDS after its most recent match,
    watching for one more — deliberately UNCONDITIONAL, not gated on
    known_pids already showing a descendant: found live, testing
    against this repo's own IFTNTSMWTISA.py fixture, that the
    updater's own window matches almost instantly, long before the
    real app's process has even been forked yet (that happens seconds
    later, mid "checking for updates...") — there is no reliable
    signal AT MATCH TIME that more windows are coming, since the only
    such signal doesn't exist yet at the moment it would need to.
    Entries whose spawned process exits nonzero are dropped
    immediately; entries past MOVE_TIMEOUT_SECONDS with no match at all
    are dropped too — both still reclaim focus first and both add a
    message to the returned failure_messages list. Settling quietly
    (SETTLE_SECONDS elapsed with no further match, the common case for
    an ordinary single-window app) is a normal, successful end state,
    not a failure — no message either way.
    own_region_id decides whether to request force_relayout (see
    CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout). See
    CLAUDE/NOTES/design-decisions.md#pending-moves-process-contract for
    the full PendingMovesResult contract and the bugs it fixes.
    """
    _enrich_pids(queue, provider, current_windows)
    _grow_known_pids(queue)
    reclaimed_focus = False
    resolved_target_regions = []
    failures = []
    still_pending = []
    for entry in queue.entries:
        if entry.get("pid") is not None:
            exit_code = _check_quick_exit(entry)
            if exit_code == 0:
                # Only hand off to app_id-tier if we never actually
                # observed a real descendant while the captured pid
                # was alive (_grow_known_pids' own docstring) — if we
                # did, known_pids-based matching is strictly more
                # informative than app_id ever was, and clearing "pid"
                # here would silently break the exact class this is
                # for (CLAUDE/NOTES/known-limitations.md
                # #fork-exec-pid-mismatch): an app whose real window's
                # app_id ALSO doesn't match the launcher's own
                # .desktop-derived hint.
                if len(entry.get("known_pids") or ()) <= 1:
                    entry["pid"] = None
            elif exit_code is not None:
                failures.append(_quick_exit_failure_message(entry, exit_code))
                if not dismissed:
                    provider.focus_self(fullscreen=fullscreen_only)
                    reclaimed_focus = True
                continue  # dropped — never added to still_pending

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
            # Deliberately UNCONDITIONAL, not gated on known_pids
            # already showing a descendant — found live, testing
            # against this repo's own IFTNTSMWTISA.py fixture: the
            # updater's own window matches almost instantly, long
            # before the real app's process has even been forked yet
            # (that happens seconds later, mid "checking for
            # updates..."). There is no reliable signal AT MATCH TIME
            # that more windows are coming — the only signal (a real
            # descendant appearing) doesn't exist yet at the moment
            # that would need it. So every entry lingers for
            # SETTLE_SECONDS after each match, watching for one more,
            # not just entries that already look like a fork/exec
            # pattern — the modest extra ~50ms-cadence polling after
            # every spawn is the accepted cost of that (see this
            # module's own docstring), same simplicity-over-
            # performance call this whole codebase already makes
            # elsewhere (main.py/GUIDE.md: "nothing is cached
            # per-frame").
            entry["last_matched_at"] = now
            still_pending.append(entry)
        elif entry.get("last_matched_at") is not None:
            if now - entry["last_matched_at"] <= SETTLE_SECONDS:
                still_pending.append(entry)
            # else: settled — quietly done, not a failure, no message.
        elif now - entry["started_at"] <= MOVE_TIMEOUT_SECONDS:
            still_pending.append(entry)
        elif not dismissed:
            # Giving up on this entry's match — see the docstring above
            # for why tuicc's own focus/fullscreen recovery must not
            # wait on that outcome.
            failures.append(_timeout_failure_message(entry))
            provider.focus_self(fullscreen=fullscreen_only)
            reclaimed_focus = True
    queue.entries = still_pending
    if not queue.entries:
        queue.claimed_ids.clear()
    return PendingMovesResult(reclaimed_focus, resolved_target_regions, failures)