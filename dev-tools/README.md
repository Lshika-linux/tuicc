# dev-tools

Not part of the shipped app — standalone scripts for reproducing and
regression-testing specific WM-integration bugs live, against a real
sway/i3 session, that unit tests can't reach (no real process tree, no
real window manager). Kept in the repo (not a scratchpad) because
they're reusable: the next session that touches `pending_moves.py`, or
anyone testing on real i3 hardware, can run them again without
rebuilding from scratch.

## IFTNTSMWTISA.py — fork/exec pid-mismatch mock app

Reproduces, on demand, the exact bug pattern documented in
`CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch` and fixed in
`pending_moves.py` — live-confirmed with Discord: an app's "updater"
process opens its own window (which tuicc's launcher correctly
matches, since the captured pid IS that window's real pid), then
backgrounds a genuinely DIFFERENT process for the real app instead of
exec-replacing itself. That second window has its own pid, several
generations removed from the one tuicc originally captured, AND a
deliberately mismatched `app_id` — the combination that used to leave
it stranded on tuicc's own workspace until `MOVE_TIMEOUT_SECONDS`
silently dropped it.

Read `IFTNTSMWTISA.py`'s own module docstring for exactly how the two
stages get their pids right (`os.execvp`/`setsid`, not just `Popen`) —
matters for faithfully reproducing the bug, not incidental detail.

### Mode 1 — fully automated (`live_verify_pending_moves.py`)

No tuicc process needs to be running; this talks to the WM directly,
the same way tuicc's own `main.py` loop would:

```bash
python3 dev-tools/live_verify_pending_moves.py
```

Edit `PROVIDER`/`TARGET_REGION` at the top of the script first —
`TARGET_REGION` needs to be a workspace neither tuicc nor the shell
running this script is currently on, so a "landed on the wrong place"
failure is unambiguous. Prints each match as it happens and a final
PASS/FAIL based on where the real windows actually ended up. This is
what caught the original fix's own remaining bug (see the git history
on `pending_moves.py` around the fork/exec fix — the first attempt
looked right in unit tests but still failed this exact script).

### Mode 2 — manual, through tuicc's own launcher UI

Closer to what a real user experiences, at the cost of needing to
drive tuicc's TUI by hand:

```bash
cp dev-tools/iftntsmwtisa.desktop.example ~/.local/share/applications/iftntsmwtisa.desktop
# then edit the Exec= line in that copy to the real absolute path of
# dev-tools/IFTNTSMWTISA.py on this machine
```

Then in tuicc: select a target workspace different from wherever tuicc
itself is running, type `IFTNTSMWTISA`, confirm. The "updater" window
should land on the target almost instantly; the "real app" window
should land on the SAME target a few seconds later. Remove the
`.desktop` file (`rm ~/.local/share/applications/iftntsmwtisa.desktop`)
when done — it's a test fixture, not something to leave installed.

### Cleanup

Both stages die on their own eventually (stage 2 runs `sleep
infinity`, so it won't) — kill leftovers with:

```bash
pkill -f iftntsmwtisa
```
