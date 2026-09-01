"""Tests for pending_moves.py — resolve_pending_move is a pure function
over Window lists, no WM connection or curses screen needed;
PendingMovesQueue/queue_restore_entry/queue_launcher_spawn/
promote_restore_queue/process are the session-level queue layer
main.py's loop drives, tested here with a fake provider (no WM
connection) and a monkeypatched spawn_detached (no real process
spawned).
"""

import os
import subprocess
import time
from pathlib import Path

import tuicc.pending_moves as pending_moves
from tuicc.model import Window
from tuicc.procmon import _ProcSample
from tuicc.wm_config_parser import WmConfigInfo
from tuicc.pending_moves import (
    resolve_pending_move,
    PendingMovesQueue,
    queue_restore_entry,
    queue_launcher_spawn,
    promote_restore_queue,
    process,
    _enrich_pids,
    _grow_known_pids,
    PID_GRACE_SECONDS,
    MOVE_TIMEOUT_SECONDS,
    RESTORE_STAGGER_SECONDS,
    SETTLE_SECONDS,
)


def _window(id, app_id, pid=None):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=(0, 0, 1, 1), pid=pid)


class _FakeProvider:
    def __init__(self, resolved_pids=None):
        self.moved = []
        self.floated = []
        self.focus_self_calls = 0
        self.focus_self_fullscreen_args = []
        self.focus_self_force_relayout_args = []
        # window_id -> pid, what resolve_pid() "discovers" for it —
        # mirrors a real provider's X11 lookup without touching X11.
        self.resolved_pids = resolved_pids or {}
        self.resolve_pid_calls = []
        self.no_focus_next_window_calls = []

    def move_window_to_region(self, window_id, region_id):
        self.moved.append((window_id, region_id))

    def set_floating_geometry(self, window_id, region_id, rect):
        self.floated.append((window_id, region_id, rect))

    def focus_self(self, fullscreen=False, force_relayout=False):
        self.focus_self_calls += 1
        self.focus_self_fullscreen_args.append(fullscreen)
        self.focus_self_force_relayout_args.append(force_relayout)

    def resolve_pid(self, window_id):
        self.resolve_pid_calls.append(window_id)
        return self.resolved_pids.get(window_id)

    def no_focus_next_window(self, pid):
        self.no_focus_next_window_calls.append(pid)


# ---------- basic matching ----------

def test_no_new_windows_returns_none():
    entry = {"known_ids": {"1", "2"}}
    current = [_window("1", "kitty"), _window("2", "firefox")]

    assert resolve_pending_move(entry, current, claimed=set()) is None


def test_single_new_window_with_no_pid_or_app_id_expectation_matches_it():
    entry = {"known_ids": {"1"}}
    current = [_window("1", "kitty"), _window("2", "firefox")]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_already_claimed_window_is_ignored():
    entry = {"known_ids": set()}
    current = [_window("1", "kitty"), _window("2", "firefox")]

    result = resolve_pending_move(entry, current, claimed={"1"})

    assert result.id == "2"


def test_only_claimed_new_windows_available_returns_none():
    entry = {"known_ids": set()}
    current = [_window("1", "kitty")]

    assert resolve_pending_move(entry, current, claimed={"1"}) is None


# ---------- pid tier ----------

def test_pid_match_wins_even_with_other_new_windows_present():
    entry = {"known_ids": set(), "pid": 555}
    current = [_window("1", "kitty", pid=111), _window("2", "kitty", pid=555)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_pid_expected_but_no_window_has_it_yet_waits_instead_of_guessing():
    # This used to fall through to "1" — wrong, since "1" might be a
    # different pending entry's window that just happens to have appeared
    # first. An entry with a pid expectation must wait for exactly that
    # pid (or eventually time out in the caller), not guess.
    entry = {"known_ids": set(), "pid": 999}
    current = [_window("1", "kitty", pid=111)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result is None


def test_pid_expected_appears_on_a_later_call_after_absent_earlier():
    # Regression for the real bug: on an early poll tick only an
    # unrelated window is visible; the expected pid shows up later. The
    # early call must not have claimed the wrong window, so the later
    # call still succeeds.
    entry = {"known_ids": set(), "pid": 999}
    tick_1 = [_window("1", "kitty", pid=111)]
    tick_2 = [_window("1", "kitty", pid=111), _window("2", "kitty", pid=999)]

    assert resolve_pending_move(entry, tick_1, claimed=set()) is None
    result = resolve_pending_move(entry, tick_2, claimed=set())
    assert result.id == "2"


# ---------- resolve_pending_move: known_pids (fork/exec descendant matching) ----------

def test_known_pids_matches_a_descendant_pid_not_just_the_exact_captured_one():
    # The real fork/exec case: entry's own "pid" (999, the captured
    # updater) never appears on any window, but its real descendant
    # (444, grown into known_pids by _grow_known_pids — see that
    # function's own tests) does.
    entry = {"known_ids": set(), "pid": 999, "known_pids": {999, 444}}
    current = [_window("1", "kitty", pid=111), _window("2", "discord", pid=444)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_known_pids_still_matches_the_exact_pid_when_present():
    entry = {"known_ids": set(), "pid": 999, "known_pids": {999}}
    current = [_window("1", "kitty", pid=111), _window("2", "discord", pid=999)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_known_pids_missing_falls_back_to_exact_pid_only():
    # An entry built by hand (no known_pids key at all — every other
    # test in this file does this) behaves exactly as before this
    # feature existed: only the exact captured pid matches.
    entry = {"known_ids": set(), "pid": 999}
    current = [_window("1", "kitty", pid=111), _window("2", "discord", pid=444)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result is None


def test_pid_none_skips_pid_tier_entirely():
    entry = {"known_ids": set(), "pid": None, "app_id": "firefox"}
    current = [_window("1", "kitty", pid=42), _window("2", "firefox", pid=43)]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


# ---------- app_id tier ----------

def test_app_id_match_used_when_no_pid_expected():
    entry = {"known_ids": set(), "app_id": "firefox"}
    current = [_window("1", "kitty"), _window("2", "firefox")]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_app_id_mismatch_waits_instead_of_guessing():
    entry = {"known_ids": set(), "app_id": "obsidian"}
    current = [_window("1", "kitty")]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result is None


def test_app_id_match_is_case_insensitive():
    # Live-confirmed real case: VS Code's .desktop declares
    # StartupWMClass=Code, but the real window's runtime app_id is
    # "code" — an exact-case match would wait forever for a window
    # that will never satisfy it.
    entry = {"known_ids": set(), "app_id": "Code"}
    current = [_window("1", "kitty"), _window("2", "code")]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_app_id_match_case_insensitive_the_other_direction_too():
    entry = {"known_ids": set(), "app_id": "firefox"}
    current = [_window("1", "kitty"), _window("2", "Firefox")]

    result = resolve_pending_move(entry, current, claimed=set())

    assert result.id == "2"


def test_pid_tier_never_falls_back_to_app_id_tier():
    # An entry with BOTH a pid and app_id expectation must still only
    # ever match on pid — app_id is not a secondary chance for the same
    # entry, only a separate strategy for entries with no pid at all.
    entry = {"known_ids": set(), "pid": 999, "app_id": "kitty"}
    current = [_window("1", "kitty", pid=111)]  # right app_id, wrong pid

    result = resolve_pending_move(entry, current, claimed=set())

    assert result is None


def test_two_entries_processed_in_order_do_not_collide_on_same_app_id():
    # Simulates what main.py's loop does: process pending entries in
    # order, claim each match before evaluating the next entry.
    entry_a = {"known_ids": set(), "app_id": "kitty"}
    entry_b = {"known_ids": set(), "app_id": "kitty"}
    current = [_window("1", "kitty"), _window("2", "kitty")]
    claimed = set()

    match_a = resolve_pending_move(entry_a, current, claimed)
    claimed.add(match_a.id)
    match_b = resolve_pending_move(entry_b, current, claimed)

    assert match_a.id != match_b.id
    assert {match_a.id, match_b.id} == {"1", "2"}


# ---------- queue_restore_entry ----------

def test_queue_restore_entry_non_floating_has_no_rect_key():
    queue = PendingMovesQueue()
    session_entry = {"target_region": "3", "app_id": "kitty"}

    queue_restore_entry(queue, session_entry, known_ids={"1"}, pid=123, now=10.0)

    entry = queue.entries[0]
    assert entry["target_region"] == "3"
    assert entry["known_ids"] == {"1"}
    assert entry["pid"] == 123
    assert entry["app_id"] == "kitty"
    assert entry["started_at"] == 10.0
    assert entry["floating"] is False
    assert "rect" not in entry


def test_queue_restore_entry_floating_carries_rect():
    queue = PendingMovesQueue()
    session_entry = {
        "target_region": "3", "app_id": "kitty", "floating": True,
        "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4,
    }

    queue_restore_entry(queue, session_entry, known_ids=set(), pid=None, now=5.0)

    entry = queue.entries[0]
    assert entry["floating"] is True
    assert entry["rect"] == (0.1, 0.2, 0.3, 0.4)


# ---------- queue_launcher_spawn ----------

def test_queue_launcher_spawn_never_carries_floating_or_rect():
    queue = PendingMovesQueue()

    queue_launcher_spawn(queue, target_region="2", known_ids={"1"}, pid=99, app_id_hint="firefox", now=1.0)

    entry = queue.entries[0]
    assert entry == {
        "target_region": "2", "known_ids": {"1"}, "pid": 99,
        "root_pid": 99, "known_pids": {99},
        "app_id": "firefox", "started_at": 1.0, "log_path": None,
    }


def test_queue_launcher_spawn_carries_log_path_when_given():
    queue = PendingMovesQueue()

    queue_launcher_spawn(
        queue, target_region="2", known_ids={"1"}, pid=99, app_id_hint="firefox", now=1.0,
        log_path=Path("/tmp/launcher_firefox_1.log"),
    )

    assert queue.entries[0]["log_path"] == Path("/tmp/launcher_firefox_1.log")


def test_queue_restore_entry_carries_log_path_when_given():
    queue = PendingMovesQueue()
    session_entry = {"target_region": "3", "app_id": "kitty"}

    queue_restore_entry(
        queue, session_entry, known_ids={"1"}, pid=123, now=10.0,
        log_path=Path("/tmp/restore_kitty_1.log"),
    )

    assert queue.entries[0]["log_path"] == Path("/tmp/restore_kitty_1.log")


# ---------- promote_restore_queue ----------

def test_promote_restore_queue_empty_queue_is_noop_and_does_not_touch_timestamp(monkeypatch):
    calls = []
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: calls.append(1) or 1)
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=5.0)

    promote_restore_queue(queue, provider, restore_queue=[], known_ids=set(), now=100.0)

    assert queue.entries == []
    assert queue.last_restore_launch == 5.0
    assert calls == []
    assert provider.no_focus_next_window_calls == []


def test_promote_restore_queue_stagger_gating_blocks_too_soon(monkeypatch):
    calls = []
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: calls.append(1) or 1)
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=10.0)
    restore_queue = [{"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"}]

    promote_restore_queue(queue, provider, restore_queue, known_ids=set(), now=10.0 + RESTORE_STAGGER_SECONDS / 2)

    assert queue.entries == []
    assert restore_queue == [{"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"}]
    assert calls == []
    assert provider.no_focus_next_window_calls == []


def test_promote_restore_queue_pops_one_and_spawns_it(monkeypatch):
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: 4242)
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=0.0)
    restore_queue = [
        {"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"},
        {"cmdline": ["firefox"], "target_region": "2", "app_id": "firefox"},
    ]

    promote_restore_queue(queue, provider, restore_queue, known_ids={"x"}, now=10.0)

    assert len(restore_queue) == 1
    assert len(queue.entries) == 1
    assert queue.entries[0]["pid"] == 4242
    assert queue.entries[0]["target_region"] == "1"


def test_promote_restore_queue_passes_a_log_path_under_spawn_log_dir(monkeypatch, tmp_path):
    # See spawn_detached's docstring and
    # CLAUDE/NOTES/known-limitations.md#restore-relaunch-crash — a saved
    # cmdline that crashes on relaunch looks identical to "never
    # started" from the outside without this captured somewhere.
    calls = []
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: calls.append(k) or 4242)
    monkeypatch.setattr(pending_moves, "SPAWN_LOG_DIR", tmp_path / "logs")
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=0.0)
    restore_queue = [{"cmdline": ["electron", "obsidian.asar"], "target_region": "2", "app_id": "obsidian"}]

    promote_restore_queue(queue, provider, restore_queue, known_ids=set(), now=10.0)

    log_path = calls[0]["log_path"]
    assert log_path.parent == tmp_path / "logs"
    assert log_path.name.startswith("restore_obsidian_")


def test_promote_restore_queue_calls_no_focus_next_window_with_spawned_pid(monkeypatch):
    # See Provider.no_focus_next_window()'s docstring — called right
    # after the pid is known, before the restored window can steal
    # focus/fullscreen from tuicc.
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: 4242)
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=0.0)
    restore_queue = [{"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"}]

    promote_restore_queue(queue, provider, restore_queue, known_ids=set(), now=10.0)

    assert provider.no_focus_next_window_calls == [4242]
    assert queue.last_restore_launch == 10.0


def test_promote_restore_queue_returns_failure_message_when_spawn_fails(monkeypatch):
    # spawn_detached() returns None when subprocess.Popen() itself
    # raised — see its own docstring. Found live: an uncaught exception
    # here used to crash tuicc's whole main loop, not just this one
    # restore entry.
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: None)
    provider = _FakeProvider()
    queue = PendingMovesQueue(last_restore_launch=0.0)
    restore_queue = [{"cmdline": ["/nonexistent"], "target_region": "1", "app_id": "obsidian"}]

    message = promote_restore_queue(queue, provider, restore_queue, known_ids=set(), now=10.0)

    assert message is not None
    assert "obsidian" in message
    assert "could not be started" in message
    assert queue.entries == []  # nothing to track — no pid, no window will ever match
    assert provider.no_focus_next_window_calls == []
    assert queue.last_restore_launch == 10.0  # still staggers a later entry correctly


# ---------- process ----------

def test_process_matches_and_moves_window():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.moved == [("1", "3")]
    # Lingers (unconditionally — see process()'s own docstring for why
    # this can't be gated on evidence that doesn't exist yet at match
    # time) for SETTLE_SECONDS watching for a second window from the
    # same spawn, rather than finalizing the instant the first one
    # matches. test_process_settles_quietly_after_settle_seconds_with_
    # no_further_match below covers the eventual quiet drain.
    assert len(queue.entries) == 1
    assert queue.entries[0]["last_matched_at"] == 1.0


def test_process_resolves_target_region_against_wm_config():
    # Found live: a real sway config using numbered+named workspaces
    # (bindsym $mod+8 workspace number 8:VIII) left tuicc creating the
    # workspace under the bare number when it was the first thing to
    # ever target it. entry["target_region"] is always the bare number
    # (session.py/queue_launcher_spawn both record it that way) —
    # process() must resolve it against wm_config before moving.
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "8", "started_at": 0.0}])
    current = [_window("1", "kitty")]
    wm_config = WmConfigInfo(workspace_names=["1:I", "8:VIII"])

    result = process(queue, provider, current, dismissed=False, now=1.0, wm_config=wm_config)

    assert provider.moved == [("1", "8:VIII")]
    assert result.resolved_target_regions == ["8:VIII"]


def test_process_wm_config_none_leaves_target_region_unchanged():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "8", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0, wm_config=None)

    assert provider.moved == [("1", "8")]


def test_process_focus_self_called_when_not_dismissed():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.focus_self_calls == 1
    assert result.reclaimed_focus is True
    assert result.resolved_target_regions == ["3"]


def test_process_defaults_to_not_fullscreen():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.focus_self_fullscreen_args == [False]


def test_process_passes_fullscreen_only_through_to_focus_self():
    # The actual fix for tuicc losing fullscreen on a spawn/restore —
    # see Provider.focus_self()'s docstring for why reclaiming focus
    # alone isn't enough.
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0, fullscreen_only=True)

    assert provider.focus_self_fullscreen_args == [True]


def test_process_fullscreen_only_ignored_while_dismissed():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=True, now=1.0, fullscreen_only=True)

    assert provider.focus_self_calls == 0
    assert provider.focus_self_fullscreen_args == []


def test_process_focus_self_not_called_when_dismissed():
    # Regression: focusing a scratchpadded tuicc window un-hides it on
    # sway/i3 — must not fire while tuicc was dismissed after the spawn
    # but before it resolved (see main.py's own `dismissed` comment).
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    result = process(queue, provider, current, dismissed=True, now=1.0)

    assert provider.moved == [("1", "3")]
    assert provider.focus_self_calls == 0
    assert result.reclaimed_focus is False
    assert result.resolved_target_regions == ["3"]  # matched+moved regardless of dismissed; only focus_self() is gated


def test_process_returns_false_when_nothing_resolves():
    # No new window at all this round — main.py's transition detector
    # must not be told to expect a self-inflicted focus reclaim that
    # never actually happens.
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no new window

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert result.reclaimed_focus is False
    assert result.resolved_target_regions == []


def test_process_floating_entry_calls_set_floating_geometry():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "floating": True, "rect": (0.1, 0.2, 0.3, 0.4),
    }])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.floated == [("1", "3", (0.1, 0.2, 0.3, 0.4))]


def test_process_no_match_within_timeout_stays_pending():
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no new window

    process(queue, provider, current, dismissed=False, now=MOVE_TIMEOUT_SECONDS - 1)

    assert queue.entries == [entry]
    assert provider.moved == []


def test_process_no_match_past_timeout_is_dropped():
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no new window

    process(queue, provider, current, dismissed=False, now=MOVE_TIMEOUT_SECONDS + 1)

    assert queue.entries == []


def test_process_reclaims_focus_when_entry_times_out_unmatched():
    # A spawn whose window never resolves (pid never enriched in time,
    # app_id mismatched its .desktop hint — the exact failure mode that
    # left tuicc stuck non-fullscreen and unfocused indefinitely, found
    # live on i3) must not hold tuicc's own recovery hostage forever:
    # give up on the match, but still reclaim focus.
    provider = _FakeProvider()
    entry = {
        "known_ids": {"1"}, "target_region": "3", "started_at": 0.0,
        "pid": 999, "app_id": "obsidian",
    }
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no matching window ever shows up

    result = process(
        queue, provider, current, dismissed=False,
        now=MOVE_TIMEOUT_SECONDS + 1, fullscreen_only=True,
    )

    assert queue.entries == []
    assert provider.moved == []
    assert provider.focus_self_calls == 1
    assert provider.focus_self_fullscreen_args == [True]
    assert result.reclaimed_focus is True
    assert result.resolved_target_regions == []  # gave up unmatched — no real destination to report
    assert result.failure_messages == ["obsidian never opened a window (timed out)"]


def test_process_does_not_reclaim_focus_on_timeout_while_dismissed():
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]

    result = process(queue, provider, current, dismissed=True, now=MOVE_TIMEOUT_SECONDS + 1)

    assert provider.focus_self_calls == 0
    assert result.reclaimed_focus is False
    assert result.resolved_target_regions == []
    assert result.failure_messages == []  # give-up path is skipped entirely while dismissed


def test_process_pid_downgrades_to_app_id_after_grace_period():
    provider = _FakeProvider()
    entry = {"known_ids": set(), "target_region": "3", "started_at": 0.0, "pid": 999, "app_id": "kitty"}
    queue = PendingMovesQueue(entries=[entry])
    # A window matching app_id but not the expected pid shows up.
    current = [_window("1", "kitty", pid=111)]

    process(queue, provider, current, dismissed=False, now=PID_GRACE_SECONDS + 0.1)

    assert provider.moved == [("1", "3")]


def test_process_claimed_ids_stay_populated_while_queue_not_fully_drained():
    provider = _FakeProvider()
    entry_a = {"known_ids": set(), "target_region": "1", "started_at": 0.0}
    entry_b = {"known_ids": {"2"}, "target_region": "2", "started_at": 0.0}  # "2" already known, never matches here
    queue = PendingMovesQueue(entries=[entry_a, entry_b])
    current = [_window("1", "kitty"), _window("2", "firefox")]

    process(queue, provider, current, dismissed=False, now=1.0)

    # entry_a now lingers too (matched, watching for a second window —
    # see process()'s own docstring) rather than vanishing outright;
    # entry_b is still waiting on its own first match. Both present.
    assert entry_a in queue.entries
    assert entry_b in queue.entries
    assert queue.claimed_ids == {"1"}


def test_process_claimed_ids_clear_once_queue_fully_drained():
    provider = _FakeProvider()
    entry = {"known_ids": set(), "target_region": "1", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)
    assert queue.claimed_ids == {"1"}  # not cleared yet — entry is lingering, watching for a second window

    # No second window ever shows up; advance past SETTLE_SECONDS so
    # the lingering entry finally settles and the queue fully drains.
    process(queue, provider, current, dismissed=False, now=1.0 + SETTLE_SECONDS + 0.1)

    assert queue.entries == []
    assert queue.claimed_ids == set()


# ---------- process: resolved_target_regions ----------
# The fix for main.py's focus_id auto-follow (see its own comment at the
# pending_moves.process() call site): focus_id/the preview panel staying
# blank forever after a session restore completed, not just during the
# transient co-location window, because expect_focus_reclaim suppresses
# the real-focus-transition reset for the entirety of a restore. main.py
# needs to know exactly which target_regions a round of process() really
# resolved a window onto, so it can move focus_id to follow.

def test_process_resolved_target_regions_lists_every_match_this_round():
    provider = _FakeProvider()
    entry_a = {"known_ids": set(), "target_region": "1", "started_at": 0.0}
    entry_b = {"known_ids": set(), "target_region": "2", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry_a, entry_b])
    current = [_window("1", "kitty"), _window("2", "firefox")]

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert result.resolved_target_regions == ["1", "2"]


def test_process_resolved_target_regions_omits_still_pending_entries():
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no new window yet — still within timeout

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert result.resolved_target_regions == []
    assert queue.entries == [entry]


def test_process_resolved_target_regions_includes_a_match_even_while_dismissed():
    # Matching/moving still happens while dismissed — only focus_self()
    # is gated. main.py's own auto-follow logic additionally checks
    # focus_id before acting on this, so a stray follow while tuicc is
    # hidden isn't a concern at this layer.
    provider = _FakeProvider()
    entry = {"known_ids": set(), "target_region": "7", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]

    result = process(queue, provider, current, dismissed=True, now=1.0)

    assert result.resolved_target_regions == ["7"]


# ---------- process: force_relayout ----------
# Separate, sway/i3-structural fix from resolved_target_regions above:
# a fullscreen tuicc suppresses tiling-layout computation for its own
# workspace entirely, so a window landing on that SAME workspace never
# gets a real rect computed for it — stuck blank in the preview — even
# once focus_id correctly points at it. force_relayout asks focus_self()
# to force a layout pass by briefly toggling fullscreen off and back on,
# but only when it's actually needed: the resolving entry's target is
# tuicc's own current region (own_region_id).

def test_process_requests_force_relayout_when_target_matches_own_region():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0, fullscreen_only=True, own_region_id="3")

    assert provider.focus_self_force_relayout_args == [True]


def test_process_does_not_request_force_relayout_for_a_different_target():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0, fullscreen_only=True, own_region_id="9")

    assert provider.focus_self_force_relayout_args == [False]


def test_process_force_relayout_compares_against_the_resolved_target_not_the_bare_one():
    # Found live: entry["target_region"] is always bare ("8"), but
    # own_region_id (loop_state.last_focused_region_id) is resolved now
    # too (see frame_update.py's own resolved_focused_region_id) -
    # comparing the bare dict value against it silently never matched
    # for a numbered+named workspace, even when they really were the
    # same region.
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "8", "started_at": 0.0}])
    current = [_window("1", "kitty")]
    wm_config = WmConfigInfo(workspace_names=["1:I", "8:VIII"])

    process(
        queue, provider, current, dismissed=False, now=1.0, fullscreen_only=True,
        own_region_id="8:VIII", wm_config=wm_config,
    )

    assert provider.focus_self_force_relayout_args == [True]


def test_process_does_not_request_force_relayout_when_own_region_id_omitted():
    # Default None — a caller that doesn't track its own region simply
    # doesn't get this fix, same as main.py's own behavior before
    # last_focused_region_id is threaded through.
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0, fullscreen_only=True)

    assert provider.focus_self_force_relayout_args == [False]


def test_process_does_not_request_force_relayout_on_give_up_timeout():
    # Nothing actually resolved onto own_region_id in the give-up path
    # — there's no new rect to fix.
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no matching window ever shows up

    process(
        queue, provider, current, dismissed=False,
        now=MOVE_TIMEOUT_SECONDS + 1, fullscreen_only=True, own_region_id="3",
    )

    assert provider.focus_self_force_relayout_args == [False]


# ---------- _enrich_pids ----------

def test_enrich_pids_fills_in_pid_for_a_new_unclaimed_window():
    # Regression: on a provider whose get_state() never supplies pid
    # (i3 — its GET_TREE has no pid field), the pid tier was
    # structurally dead — every entry burned PID_GRACE_SECONDS then
    # relied entirely on app_id matching, which fails outright for
    # apps whose real runtime app_id doesn't match their .desktop
    # entry's hint (common for Python/Electron apps launched via a
    # bare interpreter). Without this fix, a spawn's window would sit
    # wherever the WM opened it — unmoved — which also triggers tuicc
    # losing fullscreen when that happens to be tuicc's own workspace.
    provider = _FakeProvider(resolved_pids={"2": 555})
    queue = PendingMovesQueue(entries=[{"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty"), _window("2", "blanket", pid=None)]

    _enrich_pids(queue, provider, current)

    assert current[1].pid == 555
    assert provider.resolve_pid_calls == ["2"]


def test_enrich_pids_skips_windows_every_entry_already_knew_about():
    provider = _FakeProvider(resolved_pids={"1": 111})
    queue = PendingMovesQueue(entries=[{"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty", pid=None)]

    _enrich_pids(queue, provider, current)

    assert current[0].pid is None
    assert provider.resolve_pid_calls == []


def test_enrich_pids_skips_already_claimed_windows():
    provider = _FakeProvider(resolved_pids={"2": 555})
    queue = PendingMovesQueue(
        entries=[{"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}],
        claimed_ids={"2"},
    )
    current = [_window("1", "kitty"), _window("2", "blanket", pid=None)]

    _enrich_pids(queue, provider, current)

    assert current[1].pid is None
    assert provider.resolve_pid_calls == []


def test_enrich_pids_leaves_an_already_known_pid_untouched():
    provider = _FakeProvider(resolved_pids={"2": 555})
    queue = PendingMovesQueue(entries=[{"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty"), _window("2", "blanket", pid=999)]

    _enrich_pids(queue, provider, current)

    assert current[1].pid == 999
    assert provider.resolve_pid_calls == []


def test_process_matches_via_enriched_pid_on_a_provider_with_no_native_pid():
    # End-to-end: entry expects pid 555 (from spawn_detached), the
    # window's own pid starts unknown (i3-shaped), process() enriches
    # it via resolve_pid() before matching — should match immediately,
    # not wait out PID_GRACE_SECONDS and fall through to app_id.
    provider = _FakeProvider(resolved_pids={"2": 555})
    queue = PendingMovesQueue(entries=[{
        "known_ids": {"1"}, "target_region": "3", "started_at": 0.0,
        "pid": 555, "app_id": "blanket",
    }])
    current = [_window("1", "kitty"), _window("2", "python3", pid=None)]  # real app_id mismatches the hint

    process(queue, provider, current, dismissed=False, now=0.1)

    assert provider.moved == [("2", "3")]
    assert len(queue.entries) == 1  # lingers, watching for a second window — see process()'s own docstring


# ---------- process: quick-exit detection ----------
# See CLAUDE/NOTES/design-decisions.md#pending-moves-quick-exit-detection.
# Real, short-lived subprocesses here (not a mocked os.waitpid) for the
# actual exit-code tests — process lifecycle timing is exactly what's
# under test, same philosophy test_control.py's own
# _run_detached_detecting_quick_failure tests already use.

def _let_subprocess_exit(seconds=0.3):
    """A fixed wait for a trivial fast-exiting subprocess (`sh -c
    'exit N'`) to have actually exited at the OS level. Deliberately
    never calls proc.poll()/proc.wait() — either would reap the child
    itself via Python's own os.waitpid(WNOHANG) call, consuming the one
    reap _check_quick_exit's own os.waitpid() needs to see (a pid can
    only be waitpid()'d successfully once). Mirrors control.py's own
    QUICK_FAILURE_WINDOW_SECONDS=0.3 assumption that a trivial local
    process exits well within this window.
    """
    time.sleep(seconds)


def test_process_exit_0_downgrades_pid_immediately_well_before_grace_period():
    proc = subprocess.Popen(["sh", "-c", "exit 0"])
    _let_subprocess_exit()
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": proc.pid, "app_id": "kitty",
    }
    queue = PendingMovesQueue(entries=[entry])
    # A window matching app_id but NOT the (already-dead) expected pid —
    # only reachable via app_id-tier, which the old fixed-timer downgrade
    # would not have unlocked yet at this `now` (well under
    # PID_GRACE_SECONDS = 6.0).
    current = [_window("1", "kitty", pid=111)]

    result = process(queue, provider, current, dismissed=False, now=0.5)

    assert provider.moved == [("1", "3")]
    assert len(queue.entries) == 1  # lingers, watching for a second window — see process()'s own docstring
    assert result.failure_messages == []


def test_process_nonzero_exit_drops_entry_immediately_with_failure_message():
    proc = subprocess.Popen(["sh", "-c", "exit 7"])
    _let_subprocess_exit()
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": proc.pid, "app_id": "firefox",
    }
    queue = PendingMovesQueue(entries=[entry])

    result = process(queue, provider, current_windows=[], dismissed=False, now=0.1)

    assert queue.entries == []  # dropped now, not left still_pending until MOVE_TIMEOUT_SECONDS
    assert provider.moved == []
    assert provider.focus_self_calls == 1
    assert result.reclaimed_focus is True
    assert result.resolved_target_regions == []
    assert result.failure_messages == ["firefox exited (code 7)"]


def test_process_nonzero_exit_reports_failure_but_skips_focus_self_while_dismissed():
    proc = subprocess.Popen(["sh", "-c", "exit 3"])
    _let_subprocess_exit()
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": proc.pid, "app_id": "firefox",
    }
    queue = PendingMovesQueue(entries=[entry])

    result = process(queue, provider, current_windows=[], dismissed=True, now=0.1)

    assert provider.focus_self_calls == 0
    assert result.reclaimed_focus is False
    assert result.failure_messages == ["firefox exited (code 3)"]


def test_process_nonzero_exit_message_references_log_path_name_when_captured():
    proc = subprocess.Popen(["sh", "-c", "exit 1"])
    _let_subprocess_exit()
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": proc.pid, "app_id": "obsidian",
        "log_path": Path("/home/user/.config/tuicc/logs/launcher_obsidian_123.log"),
    }
    queue = PendingMovesQueue(entries=[entry])

    result = process(queue, provider, current_windows=[], dismissed=False, now=0.1)

    assert result.failure_messages == ["obsidian exited (code 1) — see launcher_obsidian_123.log"]


def test_process_still_running_pid_is_left_completely_untouched():
    proc = subprocess.Popen(["sleep", "2"])
    try:
        provider = _FakeProvider()
        entry = {
            "known_ids": {"1"}, "target_region": "3", "started_at": 0.0,
            "pid": proc.pid, "app_id": "kitty",
        }
        queue = PendingMovesQueue(entries=[entry])
        current = [_window("1", "kitty")]  # no new window — nothing to match anyway

        result = process(queue, provider, current, dismissed=False, now=0.1)

        assert entry["pid"] == proc.pid  # untouched — not downgraded
        assert "exit_code" not in entry
        assert queue.entries == [entry]
        assert result.failure_messages == []
    finally:
        proc.kill()
        proc.wait()


def test_check_quick_exit_returns_none_and_caches_when_pid_is_not_our_child():
    # os.waitpid on a pid that isn't our own direct child (here: our own
    # pid) always raises ChildProcessError — the defensive fallback path.
    entry = {"pid": os.getpid()}

    result = pending_moves._check_quick_exit(entry)

    assert result is None
    assert entry["exit_code"] is None  # cached — a second call won't retry the syscall


def test_check_quick_exit_does_not_call_waitpid_again_once_cached(monkeypatch):
    entry = {"pid": 4242, "exit_code": 7}  # already cached from a prior call

    def _boom(pid, options):
        raise AssertionError("waitpid called again on an already-cached entry")
    monkeypatch.setattr(pending_moves.os, "waitpid", _boom)

    assert pending_moves._check_quick_exit(entry) == 7


def test_check_quick_exit_returns_none_for_an_entry_with_no_pid():
    assert pending_moves._check_quick_exit({"pid": None}) is None
    assert pending_moves._check_quick_exit({}) is None


# ---------- _grow_known_pids ----------

def _sample(pid, ppid):
    return _ProcSample(pid=pid, ppid=ppid, utime=0, stime=0)


def test_grow_known_pids_adds_a_real_child_of_root_pid(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),  # real child of the captured "updater" pid
    })
    entry = {"root_pid": 100, "known_pids": {100}}
    queue = PendingMovesQueue(entries=[entry])

    _grow_known_pids(queue)

    assert entry["known_pids"] == {100, 200}


def test_grow_known_pids_accumulates_across_calls_even_after_reparenting(monkeypatch):
    # First call: catches the real parent-child link while it's intact.
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
    })
    entry = {"root_pid": 100, "known_pids": {100}}
    queue = PendingMovesQueue(entries=[entry])
    _grow_known_pids(queue)
    assert 200 in entry["known_pids"]

    # Second call: 100 has since exited, 200 reparented to init (1) — a
    # fresh subtree walk from 100 alone would find nothing new, but 200
    # must stay known from the first call (accumulate, never shrink).
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        200: _sample(200, ppid=1),
    })
    _grow_known_pids(queue)

    assert entry["known_pids"] == {100, 200}


def test_grow_known_pids_walks_grandchildren_too(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
        300: _sample(300, ppid=200),  # grandchild
    })
    entry = {"root_pid": 100, "known_pids": {100}}
    queue = PendingMovesQueue(entries=[entry])

    _grow_known_pids(queue)

    assert entry["known_pids"] == {100, 200, 300}


def test_grow_known_pids_skips_an_entry_with_no_root_pid(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {100: _sample(100, ppid=1)})
    entry = {"known_ids": set()}  # a hand-built entry, same shape older tests in this file use
    queue = PendingMovesQueue(entries=[entry])

    _grow_known_pids(queue)  # must not raise

    assert "known_pids" not in entry


def test_grow_known_pids_does_nothing_with_an_empty_queue(monkeypatch):
    def _boom():
        raise AssertionError("scan_all_processes called with nothing pending")
    monkeypatch.setattr(pending_moves, "scan_all_processes", _boom)
    queue = PendingMovesQueue(entries=[])

    _grow_known_pids(queue)  # must not scan /proc at all when there's nothing pending


def test_grow_known_pids_unrelated_processes_are_not_pulled_in(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
        999: _sample(999, ppid=1),  # unrelated process, shares no ancestry with 100
    })
    entry = {"root_pid": 100, "known_pids": {100}}
    queue = PendingMovesQueue(entries=[entry])

    _grow_known_pids(queue)

    assert 999 not in entry["known_pids"]


# ---------- process: fork/exec pid-mismatch end to end ----------
# CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch — live-confirmed
# with Discord: an updater is captured, but the real app it launches is a
# genuinely different process (forked, not exec-replaced), and its window's
# app_id doesn't match the launcher's own .desktop-derived hint either.

def test_process_matches_via_a_forked_descendant_pid_even_when_app_id_also_mismatches(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
    })
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": 100, "root_pid": 100, "known_pids": {100},
        "app_id": "discord-updater",  # deliberately doesn't match the real window below
    }
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "discord", pid=200)]  # real app's own, different pid

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.moved == [("1", "3")]
    # Stays pending, watching for a SECOND window from the same known_pids
    # tree (the real bug: the updater's own window matches first, then the
    # real app's genuinely separate window shows up seconds later) —
    # settling immediately here would silently reintroduce the exact
    # failure this whole mechanism exists to fix.
    assert len(queue.entries) == 1
    assert queue.entries[0]["last_matched_at"] == 1.0


def test_process_matches_a_second_window_from_the_same_known_pids_tree(monkeypatch):
    # The real Discord scenario end to end: the updater's window matches
    # on one frame, the real app's genuinely different window shows up
    # a few seconds later on a LATER frame — must also match and move,
    # via the same still-pending entry.
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
    })
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": 100, "root_pid": 100, "known_pids": {100},
        "app_id": "discord-updater",
    }
    queue = PendingMovesQueue(entries=[entry])

    # Frame 1: only the updater's own window (pid 100) exists.
    process(queue, provider, [_window("1", "discord-updater", pid=100)], dismissed=False, now=1.0)
    assert provider.moved == [("1", "3")]
    assert len(queue.entries) == 1

    # Frame 2, a few seconds later (within SETTLE_SECONDS): the real
    # app's window (pid 200, a known descendant by now) appears.
    process(
        queue, provider,
        [_window("1", "discord-updater", pid=100), _window("2", "discord", pid=200)],
        dismissed=False, now=3.0,
    )

    assert provider.moved == [("1", "3"), ("2", "3")]


def test_process_settles_quietly_after_settle_seconds_with_no_further_match(monkeypatch):
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {
        100: _sample(100, ppid=1),
        200: _sample(200, ppid=100),
    })
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": 100, "root_pid": 100, "known_pids": {100},
        "app_id": "discord-updater",
    }
    queue = PendingMovesQueue(entries=[entry])

    process(queue, provider, [_window("1", "discord-updater", pid=100)], dismissed=False, now=1.0)
    assert len(queue.entries) == 1

    # No second window ever shows up; well past SETTLE_SECONDS since the
    # last (only) match.
    result = process(queue, provider, [_window("1", "discord-updater", pid=100)],
                      dismissed=False, now=1.0 + pending_moves.SETTLE_SECONDS + 1)

    assert queue.entries == []
    assert result.failure_messages == []  # settling quietly is success, not a failure


def test_process_still_matches_the_exact_pid_when_no_fork_happened(monkeypatch):
    # The common case — must keep working unchanged.
    monkeypatch.setattr(pending_moves, "scan_all_processes", lambda: {100: _sample(100, ppid=1)})
    provider = _FakeProvider()
    entry = {
        "known_ids": set(), "target_region": "3", "started_at": 0.0,
        "pid": 100, "root_pid": 100, "known_pids": {100}, "app_id": "kitty",
    }
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty", pid=100)]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.moved == [("1", "3")]