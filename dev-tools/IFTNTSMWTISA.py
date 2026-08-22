#!/usr/bin/env python3
"""IFTNTSMWTISA — "I Feel The Need To Spawm More Windows Than I Should App".

Mock reproduction of the exact fork/exec pid-mismatch pattern
(CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch), live-
confirmed with Discord: an "updater" window opens and correctly
matches tuicc's launcher spawn, then backgrounds a GENUINELY DIFFERENT
process for "the real app" instead of exec-replacing itself — that
second window has its own pid AND a deliberately different app_id, so
neither of resolve_pending_move()'s first two tiers (exact pid, then
app_id) would have matched it before pending_moves.py's known_pids
fork/exec descendant tracking existed. See this dev-tools directory's
own README.md for how to actually run this (both a fully automated
mode via live_verify_pending_moves.py, and a manual one through
tuicc's own launcher UI).

How the two stages get their pids right, precisely mirroring the real
bug:

  Stage 1 (THIS process) — os.execvp()'s itself straight into kitty,
  so the pid tuicc's spawn_detached() captured (this script's own pid)
  becomes kitty's real pid. That window matches by plain exact pid,
  same as any well-behaved app — the ALREADY-working case, unaffected
  by anything this test is actually probing.

  Stage 2 (the real app) — stage 1's shell backgrounds (via `setsid`,
  not exec) a fresh `python3 IFTNTSMWTISA.py 2` — a real child process,
  several generations removed from the original captured pid by the
  time it execs into its own kitty window (kitty1 -> bash1 -> python2
  -> kitty2). setsid (not just `&`+`disown`) matters here, found live:
  a bare backgrounded job is still in kitty1's own session, and closing
  kitty1 tore stage 2 down with it despite `disown` — exactly why
  tuicc's OWN spawn_detached() uses start_new_session=True rather than
  relying on shell job control; this mirrors that. Its app_id
  ("iftntsmwtisa_real_app") is deliberately NOT what the launcher's own
  .desktop-derived hint says (see this directory's own .desktop
  template — it sets no StartupWMClass, so the hint falls back to the
  file's own basename, "iftntsmwtisa" — matching neither window). This
  is the window that used to sit unrelocated until MOVE_TIMEOUT_SECONDS
  silently dropped it; with pending_moves.py's fork/exec tracking, it
  should route to the same target region stage 1 did, a few seconds
  after mapping (see pending_moves.py's own SETTLE_SECONDS).

Assumes kitty is on PATH (Rafi's own daily terminal, same assumption
this codebase's own README/install.sh examples already make elsewhere)
— swap the "kitty" strings below for your own terminal if it supports
an equivalent --app-id/--class flag; a terminal that can't set its own
app_id per-invocation can't reproduce the "deliberately mismatched
app_id" half of this test.

Not meant to be run by hand outside the modes README.md documents — it
execs into kitty unconditionally, no fallback terminal, no error
handling; a one-shot test fixture, not a real app.
"""

import os
import sys

UPDATE_CHECK_SECONDS = 3
STAGE1_APP_ID = "iftntsmwtisa_updater"
STAGE2_APP_ID = "iftntsmwtisa_real_app"


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "1"
    script_path = os.path.abspath(__file__)

    if stage == "2":
        # THE REAL APP. Its own window, its own pid (several
        # generations removed from stage 1's), its own app_id that
        # matches neither stage 1's window NOR the launcher's own
        # .desktop-derived hint. Stays open — check which workspace
        # it landed on.
        os.execvp("kitty", [
            "kitty", "--app-id", STAGE2_APP_ID,
            "bash", "-c",
            f"echo 'STAGE 2 -- the real app'; "
            f"echo 'app_id={STAGE2_APP_ID}  pid='$$; "
            f"echo; "
            f"echo 'If this window landed on the SAME workspace stage 1 did,'; "
            f"echo 'the fork/exec fix worked. If it stuck on tuicc'\\''s own'; "
            f"echo 'workspace instead, it did not.'; "
            f"exec sleep infinity",
        ])
        return  # unreached — exec replaced this process

    # THE UPDATER. Runs AS kitty directly (exec, not spawn) so this
    # window's real pid is exactly whatever pid tuicc captured when it
    # launched this script — matches by plain exact pid, the
    # already-working case.
    os.execvp("kitty", [
        "kitty", "--app-id", STAGE1_APP_ID,
        "bash", "-c",
        f"echo 'STAGE 1 -- the updater'; "
        f"echo 'app_id={STAGE1_APP_ID}  pid='$$; "
        f"echo; "
        f"echo 'Checking for updates... ({UPDATE_CHECK_SECONDS}s)'; "
        f"sleep {UPDATE_CHECK_SECONDS}; "
        f"echo 'Update check done -- backgrounding the REAL app now'; "
        f"echo '(not exec -- a genuinely different, forked process).'; "
        f"setsid {sys.executable} {script_path} 2 >/dev/null 2>&1 </dev/null & "
        f"sleep 1; "
        f"echo 'Updater exiting.'",
    ])


if __name__ == "__main__":
    main()
