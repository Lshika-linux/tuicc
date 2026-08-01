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
"""

from tuicc.model import Window


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