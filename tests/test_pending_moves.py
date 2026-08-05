"""Tests for pending_moves.py — resolve_pending_move is a pure function
over Window lists, no WM connection or curses screen needed;
PendingMovesQueue/queue_restore_entry/queue_launcher_spawn/
promote_restore_queue/process are the session-level queue layer
main.py's loop drives, tested here with a fake provider (no WM
connection) and a monkeypatched spawn_detached (no real process
spawned).
"""

import tuicc.pending_moves as pending_moves
from tuicc.model import Window
from tuicc.pending_moves import (
    resolve_pending_move,
    PendingMovesQueue,
    queue_restore_entry,
    queue_launcher_spawn,
    promote_restore_queue,
    process,
    PID_GRACE_SECONDS,
    MOVE_TIMEOUT_SECONDS,
    RESTORE_STAGGER_SECONDS,
)


def _window(id, app_id, pid=None):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=(0, 0, 1, 1), pid=pid)


class _FakeProvider:
    def __init__(self):
        self.moved = []
        self.floated = []
        self.focus_self_calls = 0

    def move_window_to_region(self, window_id, region_id):
        self.moved.append((window_id, region_id))

    def set_floating_geometry(self, window_id, region_id, rect):
        self.floated.append((window_id, region_id, rect))

    def focus_self(self):
        self.focus_self_calls += 1


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
        "app_id": "firefox", "started_at": 1.0,
    }


# ---------- promote_restore_queue ----------

def test_promote_restore_queue_empty_queue_is_noop_and_does_not_touch_timestamp(monkeypatch):
    calls = []
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: calls.append(1) or 1)
    queue = PendingMovesQueue(last_restore_launch=5.0)

    promote_restore_queue(queue, restore_queue=[], known_ids=set(), now=100.0)

    assert queue.entries == []
    assert queue.last_restore_launch == 5.0
    assert calls == []


def test_promote_restore_queue_stagger_gating_blocks_too_soon(monkeypatch):
    calls = []
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: calls.append(1) or 1)
    queue = PendingMovesQueue(last_restore_launch=10.0)
    restore_queue = [{"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"}]

    promote_restore_queue(queue, restore_queue, known_ids=set(), now=10.0 + RESTORE_STAGGER_SECONDS / 2)

    assert queue.entries == []
    assert restore_queue == [{"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"}]
    assert calls == []


def test_promote_restore_queue_pops_one_and_spawns_it(monkeypatch):
    monkeypatch.setattr(pending_moves, "spawn_detached", lambda *a, **k: 4242)
    queue = PendingMovesQueue(last_restore_launch=0.0)
    restore_queue = [
        {"cmdline": ["kitty"], "target_region": "1", "app_id": "kitty"},
        {"cmdline": ["firefox"], "target_region": "2", "app_id": "firefox"},
    ]

    promote_restore_queue(queue, restore_queue, known_ids={"x"}, now=10.0)

    assert len(restore_queue) == 1
    assert len(queue.entries) == 1
    assert queue.entries[0]["pid"] == 4242
    assert queue.entries[0]["target_region"] == "1"
    assert queue.last_restore_launch == 10.0


# ---------- process ----------

def test_process_matches_and_moves_window():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.moved == [("1", "3")]
    assert queue.entries == []


def test_process_focus_self_called_when_not_dismissed():
    provider = _FakeProvider()
    queue = PendingMovesQueue(entries=[{"known_ids": set(), "target_region": "3", "started_at": 0.0}])
    current = [_window("1", "kitty")]

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert provider.focus_self_calls == 1
    assert result is True


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
    assert result is False


def test_process_returns_false_when_nothing_resolves():
    # No new window at all this round — main.py's transition detector
    # must not be told to expect a self-inflicted focus reclaim that
    # never actually happens.
    provider = _FakeProvider()
    entry = {"known_ids": {"1"}, "target_region": "3", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]  # no new window

    result = process(queue, provider, current, dismissed=False, now=1.0)

    assert result is False


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

    assert queue.entries == [entry_b]
    assert queue.claimed_ids == {"1"}


def test_process_claimed_ids_clear_once_queue_fully_drained():
    provider = _FakeProvider()
    entry = {"known_ids": set(), "target_region": "1", "started_at": 0.0}
    queue = PendingMovesQueue(entries=[entry])
    current = [_window("1", "kitty")]

    process(queue, provider, current, dismissed=False, now=1.0)

    assert queue.entries == []
    assert queue.claimed_ids == set()