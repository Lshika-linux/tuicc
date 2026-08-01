"""Tests for pending_moves.py — resolve_pending_move is a pure function
over Window lists, no WM connection or curses screen needed.
"""

from tuicc.model import Window
from tuicc.pending_moves import resolve_pending_move


def _window(id, app_id, pid=None):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=(0, 0, 1, 1), pid=pid)


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