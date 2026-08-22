"""Drives pending_moves.py's real process() loop against a REAL, live
WM session (no mocking), exactly the way main.py's own loop does every
frame, WITHOUT needing to drive tuicc's actual curses TUI. Spawns
IFTNTSMWTISA.py (this same directory's fork/exec pid-mismatch mock
app) targeting a workspace neither tuicc nor this shell is currently
on, then polls at the same ~50ms cadence main.py uses while anything's
pending, for long enough to see both stage 1 (updater) and stage 2
(real app) resolve — see this directory's own README.md.

Needs a real sway or i3 session to run against (set PROVIDER below to
match — see providers/registry.py's build_provider() for the full
list). No tuicc process needs to be running; this script talks to the
WM directly, the same way tuicc's own main.py loop would.
"""
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tuicc import pending_moves
from tuicc.actions import spawn_detached
from tuicc.providers.registry import build_provider

PROVIDER = "sway"  # or "i3" — see providers/registry.py
TARGET_REGION = "5"  # pick a workspace neither tuicc nor this shell is currently on
RUN_SECONDS = 12.0

provider = build_provider(PROVIDER)
state_before = provider.get_state()
known_ids = {w.id for r in state_before.regions for w in r.windows}
print(f"known_ids before spawn: {len(known_ids)} windows")

pid = spawn_detached(
    [sys.executable, str(Path(__file__).resolve().parent / "IFTNTSMWTISA.py")],
    shell_true=False,
)
print(f"spawned IFTNTSMWTISA.py, captured pid={pid}")
provider.no_focus_next_window(pid)

queue = pending_moves.PendingMovesQueue()
pending_moves.queue_launcher_spawn(queue, TARGET_REGION, known_ids, pid, "iftntsmwtisa", time.monotonic())

start = time.monotonic()
seen_moves = []
while time.monotonic() - start < RUN_SECONDS:
    state = provider.get_state()
    current_windows = [w for r in state.regions for w in r.windows]
    result = pending_moves.process(
        queue, provider, current_windows, dismissed=False, now=time.monotonic(),
    )
    if result.resolved_target_regions:
        seen_moves.append((time.monotonic() - start, result.resolved_target_regions))
        print(f"  t={time.monotonic()-start:.2f}s matched+moved -> {result.resolved_target_regions}")
    if result.failure_messages:
        print(f"  t={time.monotonic()-start:.2f}s FAILURE: {result.failure_messages}")
    if not queue.entries and seen_moves:
        # Queue fully settled (SETTLE_SECONDS elapsed with no further
        # match) after at least one real match — stop early instead of
        # always burning the full RUN_SECONDS.
        break
    time.sleep(0.05)

print()
print(f"Total matches over the run: {len(seen_moves)}")
print(f"queue.entries remaining: {len(queue.entries)}  (nonzero = still settling, or genuinely stuck)")

# Final ground truth: ask the WM directly which region each
# iftntsmwtisa window is actually on right now.
final_state = provider.get_state()
found = []
for r in final_state.regions:
    for w in r.windows:
        if "iftntsmwtisa" in (w.app_id or ""):
            found.append((w.app_id, r.id, w.pid))
print("Final real window placement (app_id, region, pid):")
for f in found:
    print(" ", f)
if not found:
    print("  (none found — either both windows already closed, or nothing ever matched)")

expected = 2  # stage 1 (updater, closes itself after ~4s) may already be gone by the time this prints
wrong_region = [f for f in found if f[1] != TARGET_REGION]
if wrong_region:
    print()
    print(f"FAIL: {len(wrong_region)} window(s) NOT on target region {TARGET_REGION!r}: {wrong_region}")
elif found:
    print()
    print(f"OK: every remaining iftntsmwtisa window is on the target region {TARGET_REGION!r}.")
