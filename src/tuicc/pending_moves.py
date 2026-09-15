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
from tuicc.winrestore import resolve_launch_argv
from tuicc.wm_config_parser import resolve_workspace_target

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

    resolved_tags accumulates {tag: matched_window_id} for every entry
    that carried a "tag" (see queue_restore_entry's own docstring) and
    matched — populated by process() below, consumed (popped) by
    winrestore.advance_tree_build()'s own per-frame tick. Persists on
    the queue itself, not just process()'s return value, because the
    tag's OWN consumer (the tiled-tree builder) runs BEFORE process()
    each frame (see frame_update.py's own call order) — a tag that
    resolves on frame N is only visible to the builder on frame N+1,
    exactly the same one-frame lag every other "drive a queue one step
    per frame" mechanism in this codebase already has. Ordinary
    launcher spawns and flat winrestore entries never set a tag, so
    they never appear here — zero behavior change for either.
    """
    entries: list = field(default_factory=list)
    claimed_ids: set = field(default_factory=set)
    last_restore_launch: float = 0.0
    resolved_tags: dict = field(default_factory=dict)


def queue_restore_entry(
    queue: PendingMovesQueue, session_entry: dict, known_ids: set, pid, now: float,
    log_path: Path | None = None,
) -> None:
    """Appends one entry for a winrestore spawn. Carries floating+rect
    when the saved window was floating (winrestore.py's saved shape)
    — queue_launcher_spawn below never does, since the launcher has no
    saved geometry to restore. log_path mirrors queue_launcher_spawn's
    own param, same reason — promote_restore_queue below already
    captures spawn_detached()'s output for a different reason
    (CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash);
    threading the same path through here means a fast nonzero-exit
    failure toast can reference it too, not just launcher spawns.

    pid is deliberately passed as None by promote_restore_queue's own
    caller for every winrestore entry, even though spawn_detached()
    really did return a real pid — winrestore entries relaunch by
    app_id (via resolve_launch_argv(), never a captured cmdline
    anymore) and match by app_id-tier only (resolve_pending_move()'s
    second tier below), on purpose: nothing here should ever depend on
    the spawned process's pid matching the eventual window's owning
    pid, which is exactly the assumption that made the fork/exec
    pid-mismatch class of failure (CLAUDE/NOTES/known-limitations.md
    #fork-exec-pid-mismatch) possible in the first place. Passing None
    here reuses resolve_pending_move()'s existing, already-tested
    app_id tier outright rather than needing a second matcher — see
    CLAUDE/NOTES/design-decisions.md#winrestore-app-id-capture.
    queue_launcher_spawn below is unaffected — a plain typed launch
    still passes its real pid and still gets pid-tier matching, since
    an ordinary launcher spawn has no reason to give that up.
    root_pid/known_pids: see resolve_pending_move()'s own docstring —
    root_pid is the immutable seed process() walks the descendant tree
    from every frame (independent of "pid" above, which DOES get
    cleared on a tier downgrade); known_pids is what actually gets
    matched against, grown (never shrunk) as real descendants are
    observed. Both stay empty/None here since pid is always None for
    this call site now.

    placed_by_wm: dead weight from the retired append_layout mechanism
    (CLAUDE/NOTES/design-decisions.md#append-layout-tiled-restore) —
    every winrestore entry gets placed the ordinary way now, so this is
    always False/absent in practice, but process() below still honors
    it if a caller ever sets it (cheaper to leave the one-line check in
    than to rip out a still-correct escape hatch).

    tag (default None): an opaque, caller-chosen label — set only by
    winrestore.py's tree-builder (advance_tree_build(), one leaf at a
    time, never more than one tagged entry in flight simultaneously) so
    it can tell WHICH of its own build steps a resolved window belongs
    to. process() copies a matched entry's tag into
    queue.resolved_tags[tag] = window_id; an entry with no tag (every
    ordinary launcher spawn, every flat winrestore leaf) never touches
    that dict at all.
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
        "placed_by_wm": session_entry.get("placed_by_wm", False),
        "tag": session_entry.get("tag"),
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
    log_path: Path | None = None, tag: str | None = None,
) -> None:
    """Appends one entry for a launcher-confirmed spawn — never carries
    floating/rect, unlike queue_restore_entry's entries. log_path (when
    the caller captured spawn_detached()'s output) is read back by
    _quick_exit_failure_message() on a fast nonzero-exit give-up, so the
    user's failure toast can point at real captured stderr instead of
    just an exit code. root_pid/known_pids — see queue_restore_entry's
    own docstring, same reasoning.

    tag (default None): set only when the launcher's own placement-mode
    picker (modules/launcher.py's LauncherState.placement_mode) asked
    for anything other than plain `tiled` placement — main.py registers
    a matching entry on a PlacementQueue before calling this, and
    advance_placements() acts on it once process() resolves this tag
    into queue.resolved_tags. Same mechanism queue_restore_entry's own
    tag already uses — see PendingMovesQueue.resolved_tags' docstring.
    """
    queue.entries.append({
        "target_region": target_region,
        "known_ids": known_ids,
        "pid": pid,
        "root_pid": pid,
        "known_pids": {pid} if pid is not None else set(),
        "app_id": app_id_hint,
        "started_at": now,
        "tag": tag,
        "log_path": log_path,
    })


def promote_restore_queue(queue: PendingMovesQueue, provider, restore_queue: list, known_ids: set, now: float, desktop_apps=()) -> str | None:
    """Pops one entry off restore_queue and spawns it, staggered by
    RESTORE_STAGGER_SECONDS. No-ops if restore_queue is empty (checked
    before the stagger-time comparison, so an empty queue never blocks
    a later real restore on a stale timestamp).

    Relaunches via resolve_launch_argv() (winrestore.py) — the app's
    own .desktop Exec= command, the same reliable, $PATH-resolved path
    launcher.py's own spawns already use — rather than ever replaying a
    captured raw argv (see CLAUDE/NOTES/design-decisions.md
    #winrestore-app-id-capture for why that approach kept breaking on
    real machines). desktop_apps is launcher.get_apps()'s own cached
    list; default () means "no match possible", same as an app with no
    .desktop entry at all.

    Returns a one-line failure message when nothing could be relaunched
    at all — either no matching .desktop entry (see
    resolve_launch_argv()) or spawn_detached() itself couldn't start
    the process (see _spawn_failure_message) — None on an ordinary
    successful spawn (the overwhelmingly common case) or when nothing
    was due this frame. On failure, nothing gets queued — no window
    will ever match this entry — so the caller doesn't need to do
    anything with queue.entries itself.
    """
    if not restore_queue:
        return None
    if now - queue.last_restore_launch < RESTORE_STAGGER_SECONDS:
        return None
    session_entry = restore_queue.pop(0)
    queue.last_restore_launch = now
    argv = resolve_launch_argv(session_entry["app_id"], desktop_apps)
    if argv is None:
        return f"{session_entry['app_id']} could not be started: no launcher entry found"
    log_path = SPAWN_LOG_DIR / f"winrestore_{session_entry['app_id']}_{int(time.time())}.log"
    pid = spawn_detached(argv, shell_true=False, log_path=log_path)
    if pid is None:
        return _spawn_failure_message(session_entry, log_path)
    # See Provider.no_focus_next_window()'s docstring — called right
    # after the pid is known, well before the restored window has had a
    # chance to map and steal focus/fullscreen from tuicc. Unrelated to
    # matching (see queue_restore_entry()'s own docstring for why pid
    # itself is NOT threaded through below) — this is a one-shot WM
    # for_window rule keyed on the real spawned pid, still valid even
    # though matching itself now ignores pid entirely.
    provider.no_focus_next_window(pid)
    queue_restore_entry(queue, session_entry, known_ids, None, now, log_path)
    return None


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


def _spawn_failure_message(session_entry: dict, log_path: Path) -> str:
    """Distinct from both _quick_exit_failure_message (got a pid, exited
    fast) and _timeout_failure_message (got a pid, no window ever
    appeared) below — this one never got a pid at all:
    actions.spawn_detached() itself returned None, meaning
    subprocess.Popen() raised (see that function's own docstring for
    when — a missing/unreadable executable is the common case, e.g. a
    saved cmdline pointing at a path that no longer resolves to
    anything real). spawn_detached() already wrote the real exception
    text into log_path for us, same "point at the log, don't embed
    detail here" convention the other two messages in this module use.
    """
    label = session_entry.get("app_id") or "Command"
    return f"{label} could not be started — see {log_path.name}"


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
    own_region_id: str | None = None, wm_config=None,
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
    CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout). wm_config
    (None degrades to today's exact behavior) resolves a matched
    entry's bare target_region against its known full workspace name
    before issuing the move — see resolve_workspace_target()'s own
    docstring for why. See
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
            # entry["target_region"] is always a bare workspace number
            # (winrestore.py/queue_launcher_spawn both record it that
            # way) — resolve it against wm_config's own parsed full
            # names first, so a target that doesn't live-exist yet gets
            # CREATED under the user's configured name (e.g. "8:VIII")
            # instead of a same-numbered bare one. Self-healing for
            # layouts saved before this existed too — see
            # wm_config_parser.resolve_workspace_target()'s own
            # docstring. wm_config=None (no autodetect data) leaves
            # target_region unchanged, today's exact behavior.
            target_region = resolve_workspace_target(
                entry["target_region"], wm_config.workspace_names if wm_config is not None else None,
            )
            # placed_by_wm: dead weight from the retired append_layout
            # mechanism (see queue_restore_entry()'s own docstring) —
            # every real entry today reaches this unset/False, but the
            # skip stays correct if anything ever sets it again.
            if not entry.get("placed_by_wm"):
                # Floating BEFORE the workspace move, not after — a
                # container's `floating enable` re-homes it onto
                # whatever workspace is CURRENTLY FOCUSED (real,
                # live-confirmed sway behavior, see CLAUDE/NOTES/
                # design-decisions.md#floating-enable-workspace-
                # reparenting), so doing the move first only gets
                # silently undone the moment set_floating_geometry()
                # runs. A floating container's own geometry survives a
                # SUBSEQUENT move just fine (confirmed live) — this
                # order is the fix, not a redundant move needed here.
                if entry.get("floating"):
                    provider.set_floating_geometry(match.id, entry["target_region"], entry["rect"])
                provider.move_window_to_region(match.id, target_region)
            if entry.get("tag") is not None:
                # See PendingMovesQueue.resolved_tags' own docstring —
                # winrestore.py's tree-builder pops this on a LATER
                # frame (it runs before process() each frame_update()
                # call), never this same one.
                queue.resolved_tags[entry["tag"]] = match.id
            resolved_target_regions.append(target_region)
            if not dismissed:
                # target_region (resolved above), not entry["target_region"]
                # (always bare) — own_region_id is loop_state.
                # last_focused_region_id, itself resolved now too (see
                # frame_update.py's own resolved_focused_region_id) —
                # comparing the bare dict value against it would silently
                # never match for a numbered+named workspace.
                force_relayout = own_region_id is not None and target_region == own_region_id
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


# Centered, no-saved-geometry default for a freshly launched floating
# window — unlike winrestore's own floating restore (a real saved
# normalized rect), a plain launcher spawn with `floating` chosen from
# the placement-mode picker has nothing to restore, just a reasonable
# starting size/position. Normalized 0..1, same space
# set_floating_geometry() already expects.
DEFAULT_FLOATING_RECT = (0.25, 0.25, 0.5, 0.5)

# How far (in the same 0..1 normalized space) each successive
# back-to-back floating spawn shifts down-and-right from
# DEFAULT_FLOATING_RECT, and how many steps before it wraps back to 0 —
# see cascade_floating_rect()'s own docstring for why these two values
# specifically.
FLOATING_CASCADE_STEP = 0.04
FLOATING_CASCADE_STEPS = 6


def cascade_floating_rect(index: int) -> tuple[float, float, float, float]:
    """DEFAULT_FLOATING_RECT, offset down-and-right by FLOATING_CASCADE_STEP
    * index (wrapping every FLOATING_CASCADE_STEPS) — so spawning several
    `floating`-placed windows back-to-back doesn't stack them exactly on
    top of each other, the same "cascade" convention real desktop
    environments use for a freshly-mapped window with no saved position.
    Only x/y move, w/h stay fixed: DEFAULT_FLOATING_RECT's own x=y=0.25,
    w=h=0.5 already leaves exactly 0.25 of slack before x+w or y+h would
    push the window off the right/bottom edge, so FLOATING_CASCADE_STEPS
    (6) * FLOATING_CASCADE_STEP (0.04) == 0.24 fits inside that slack
    with room to spare, then wraps rather than walking further.
    """
    x, y, w, h = DEFAULT_FLOATING_RECT
    offset = (index % FLOATING_CASCADE_STEPS) * FLOATING_CASCADE_STEP
    return (x + offset, y + offset, w, h)


@dataclass
class PendingPlacement:
    """One launcher spawn's own extra placement action, held until its
    tag resolves — see PlacementQueue's own docstring. mode is one of
    "stack_new"/"tab_new" (Provider.set_container_layout()), an
    existing group's label like "S1"/"T1" (Provider.move_window_to_group(),
    container_id set), or "floating" (Provider.set_floating_geometry());
    "tiled" never reaches here at all — see queue_launcher_spawn()'s own
    docstring, main.py only registers a PendingPlacement for anything
    OTHER than plain tiled placement. rect is only meaningful for
    "floating" — the caller's own cascaded DEFAULT_FLOATING_RECT (see
    PlacementQueue.floating_index) — None falls back to the plain,
    un-cascaded DEFAULT_FLOATING_RECT (e.g. a hand-built entry in tests).
    """
    mode: str
    container_id: str | None
    region_id: str
    rect: tuple[float, float, float, float] | None = None


@dataclass
class PlacementQueue:
    """Drives the launcher's own placement-mode picker (modules/
    launcher.py's LauncherState.placement_mode) — see CLAUDE/NOTES/
    design-decisions.md#launcher-placement-mode. Reuses the exact same
    tag/resolved_tags plumbing winrestore.py's TreeBuildState already
    established (PendingMovesQueue.resolved_tags' own docstring) rather
    than inventing a second mechanism: main.py registers one
    PendingPlacement per tagged launcher spawn here, right before
    calling queue_launcher_spawn() with that same tag; advance_placements()
    below drains it once pending_moves.process() resolves the tag.
    Unlike winrestore's tree-builder, MULTIPLE tags can be pending here
    simultaneously (several launcher spawns in flight at once is
    already an ordinary, supported case — see GUIDE.md's own
    verification checklist), so this is a plain dict, not a
    single-slot state machine.

    floating_index counts every `floating`-mode PendingPlacement ever
    registered (main.py increments it right there, before queuing the
    spawn) — never reset, just cycled by cascade_floating_rect()'s own
    modulo, so a run of back-to-back floating spawns cascades down-and-
    right instead of stacking on top of each other. Incrementing at
    registration time, not once the placement actually resolves, means
    several floating spawns confirmed in a row each get a distinct
    offset regardless of which one's window happens to map first.
    """
    pending: dict = field(default_factory=dict)
    floating_index: int = 0


def advance_placements(state: PlacementQueue, moves: PendingMovesQueue, provider) -> None:
    """Call every frame (frame_update.py, same spot advance_tiled_restore()/
    process() already run) — a cheap no-op whenever nothing's pending.
    For every tag in state.pending that has now resolved in
    moves.resolved_tags, pops both and dispatches the one matching
    action: "stack_new"/"tab_new" -> Provider.set_container_layout()
    (grouping the freshly-placed window with whatever's already on that
    workspace — real mod4+S semantics, see set_container_layout()'s own
    docstring); an existing group's label -> Provider.move_window_to_group()
    (precise, mark-based targeting of ONE specific group, not just
    "whatever's there"); "floating" -> Provider.set_floating_geometry()
    with placement.rect (main.py's own cascaded offset — see
    PlacementQueue.floating_index — falling back to plain
    DEFAULT_FLOATING_RECT if unset). A provider that doesn't support the
    needed method just no-ops via its own default (False/None) — no
    further fallback needed here, since main.py's own confirm-time
    resolution (handle_launcher()) already downgrades to plain `tiled`
    (never registers a PendingPlacement at all) whenever the provider
    can't support the picked mode, checked once, right there.
    """
    if not state.pending:
        return
    resolved = [tag for tag in state.pending if tag in moves.resolved_tags]
    for tag in resolved:
        placement = state.pending.pop(tag)
        window_id = moves.resolved_tags.pop(tag)
        if placement.mode == "stack_new":
            provider.set_container_layout(window_id, "stacked")
        elif placement.mode == "tab_new":
            provider.set_container_layout(window_id, "tabbed")
        elif placement.mode == "floating":
            provider.set_floating_geometry(window_id, placement.region_id, placement.rect or DEFAULT_FLOATING_RECT)
            # set_floating_geometry()'s own `floating enable` re-homes
            # the container onto WHATEVER'S CURRENTLY FOCUSED — a real,
            # live-confirmed sway behavior (CLAUDE/NOTES/design-
            # decisions.md#floating-enable-workspace-reparenting), not a
            # bug in this call itself — undoing the ordinary
            # move_window_to_region() process() already made for this
            # entry moments ago. A floating container's own geometry
            # and floating-ness survive being moved again afterward
            # (confirmed live), so re-issuing the move right here is
            # the fix — cheaper than reordering process()'s own,
            # already-unconditional move for every entry just to save
            # one extra IPC call on this one mode.
            provider.move_window_to_region(window_id, placement.region_id)
        elif placement.container_id is not None:
            provider.move_window_to_group(window_id, placement.container_id)