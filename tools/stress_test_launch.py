#!/usr/bin/env python3
"""Stress-test tuicc's pending_moves matching (src/tuicc/pending_moves.py)
against a burst of launches targeting several different workspaces at
once — something tuicc's own launcher UI can't reproduce today (you have
to Tab between different-target launches, which naturally spaces them
out), but the planned restore feature will do on purpose.

Uses tuicc's real code paths directly (SwayProvider, spawn_detached,
resolve_pending_move) — this is not a separate reimplementation, it's
driving the same mechanism main.py's loop uses, just from a standalone
script instead of the interactive curses loop.

Usage: python3 tools/stress_test_launch.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from i3ipc import Connection
from tuicc.providers.sway import SwayProvider, parse_tree
from tuicc.actions import spawn_detached
from tuicc.pending_moves import resolve_pending_move

# (command, target workspace) — 10 launches across 3 workspaces, all
# `kitty` so there's no single-instance-app confound (see the Obsidian
# finding from testing tileroot earlier).
PLAN = [
    ("kitty", "5"), ("kitty", "5"), ("kitty", "5"),
    ("kitty", "6"), ("kitty", "6"), ("kitty", "6"),
    ("kitty", "7"), ("kitty", "7"), ("kitty", "7"), ("kitty", "7"),
]

conn = Connection()
provider = SwayProvider(conn=conn)

state = parse_tree(conn.get_tree())
shared_known_ids = {w.id for r in state.regions for w in r.windows}

print(f"Launching {len(PLAN)} processes, no stagger — worst case for the matcher...")

pending = []
for cmd, target in PLAN:
    pid = spawn_detached(cmd, shell_true=False)
    pending.append({
        "target_region": target,
        "known_ids": shared_known_ids,  # ONE snapshot before the whole burst — worst case
        "pid": pid,
        "app_id": "kitty",
        "started_at": time.monotonic(),
    })
    print(f"  spawned pid={pid} -> intended for workspace {target}")

print("\nResolving matches...")

claimed = set()
deadline = time.monotonic() + 10
while pending and time.monotonic() < deadline:
    state = parse_tree(conn.get_tree())
    current_windows = [w for r in state.regions for w in r.windows]
    still_pending = []
    for entry in pending:
        match = resolve_pending_move(entry, current_windows, claimed)
        if match is not None:
            claimed.add(match.id)
            provider.move_window_to_region(match.id, entry["target_region"])
            print(f"  window {match.id} (pid={match.pid}) -> workspace {entry['target_region']}")
        else:
            still_pending.append(entry)
    pending = still_pending
    time.sleep(0.05)

if pending:
    print(f"\n{len(pending)} launches never resolved (timeout) — target(s): "
          f"{[e['target_region'] for e in pending]}")
else:
    print("\nAll 10 resolved.")

print("\nNow check swaymsg -t get_tree (or the sidebar) — did each workspace "
      "get the right count (3/3/4)?")