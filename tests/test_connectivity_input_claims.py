"""Tests for modules/connectivity.py's two R4 input-claim quartets —
wifi passphrase entry and bluetooth pairing confirm. Module-level
state, no I/O, same testability class as sessions.py's own naming
quartet.

Both flows are a small state machine now (typing -> waiting -> error-
or-close), not a single-shot "answer then close" — found live, reported
directly: the original version closed the overlay the instant an
answer was submitted, before the real connect/pair result was even
known, so a wrong wifi passphrase produced total silence. See
mark_passphrase_submitted()/set_passphrase_error()'s own docstrings
(and the equivalent pairing functions) for the full reasoning.
"""

import curses

import tuicc.modules.connectivity as connectivity_module
from tuicc.modules.connectivity import (
    apply_passphrase,
    cancel_pairing_confirm,
    cancel_passphrase_entry,
    current_pairing_request,
    entering_passphrase_ssid,
    handle_passphrase_key,
    is_confirming_pairing,
    is_entering_passphrase,
    is_pairing_waiting,
    is_passphrase_waiting,
    mark_pairing_submitted,
    mark_passphrase_submitted,
    pairing_error,
    passphrase_error,
    set_pairing_error,
    set_passphrase_error,
    start_pairing_confirm,
    start_passphrase_entry,
)


def setup_function():
    # Module-level state — reset between tests so one test's leftover
    # in-progress entry can't leak into the next.
    cancel_passphrase_entry()
    cancel_pairing_confirm()


# ---------- passphrase entry: typing ----------

def test_not_entering_passphrase_initially():
    assert is_entering_passphrase() is False


def test_start_passphrase_entry_claims_it():
    start_passphrase_entry("Home")

    assert is_entering_passphrase() is True
    assert entering_passphrase_ssid() == "Home"


def test_handle_passphrase_key_appends_printable_chars():
    start_passphrase_entry("Home")

    for ch in "hunter2":
        handle_passphrase_key(ord(ch))

    assert apply_passphrase() == "hunter2"


def test_handle_passphrase_key_backspace_removes_last_char():
    start_passphrase_entry("Home")
    handle_passphrase_key(ord("a"))
    handle_passphrase_key(ord("b"))

    handle_passphrase_key(curses.KEY_BACKSPACE)

    assert apply_passphrase() == "a"


def test_handle_passphrase_key_escape_returns_false():
    start_passphrase_entry("Home")

    still_claiming = handle_passphrase_key(27)

    assert still_claiming is False


def test_apply_passphrase_does_not_clear_state():
    # Unlike sessions.py's apply_naming(), this must NOT end the flow —
    # main.py still needs is_entering_passphrase()/the ssid while it
    # waits for the real connect result.
    start_passphrase_entry("Home")
    handle_passphrase_key(ord("x"))

    apply_passphrase()

    assert is_entering_passphrase() is True


def test_apply_passphrase_when_nothing_in_progress_returns_none():
    assert apply_passphrase() is None


def test_apply_passphrase_can_be_empty_string_not_none():
    # An empty submit is a real answer (main.py hands it to
    # IwdAgent.reply_passphrase() as-is) — distinct from None, which
    # means "nothing was in progress at all".
    start_passphrase_entry("Home")

    assert apply_passphrase() == ""


def test_cancel_passphrase_entry_discards_in_progress_state():
    start_passphrase_entry("Home")
    handle_passphrase_key(ord("x"))

    cancel_passphrase_entry()

    assert is_entering_passphrase() is False
    assert apply_passphrase() is None


def test_start_passphrase_entry_resets_input_from_a_previous_entry():
    start_passphrase_entry("Home")
    handle_passphrase_key(ord("x"))

    start_passphrase_entry("Office")

    assert apply_passphrase() == ""
    assert entering_passphrase_ssid() == "Office"


# ---------- passphrase entry: waiting / error (the actual bug fix) ----------

def test_not_waiting_or_erroring_right_after_start():
    start_passphrase_entry("Home")

    assert is_passphrase_waiting() is False
    assert passphrase_error() is None


def test_mark_passphrase_submitted_enters_waiting_state():
    start_passphrase_entry("Home")
    handle_passphrase_key(ord("x"))

    mark_passphrase_submitted()

    assert is_passphrase_waiting() is True


def test_set_passphrase_error_ends_waiting_and_records_the_message():
    start_passphrase_entry("Home")
    mark_passphrase_submitted()

    set_passphrase_error("4-Way Handshake failed")

    assert is_passphrase_waiting() is False
    assert passphrase_error() == "4-Way Handshake failed"
    # Still "entering" — the flow isn't over, the error is shown IN
    # the same overlay, not silently dropped.
    assert is_entering_passphrase() is True


def test_start_passphrase_entry_after_an_error_resets_it():
    # A retry — iwd asking again after a wrong passphrase calls
    # start_passphrase_entry() a second time (see main.py's own
    # "connectivity_passphrase" notice block) — must clear the
    # previous attempt's error and waiting state, back to plain typing.
    start_passphrase_entry("Home")
    mark_passphrase_submitted()
    set_passphrase_error("wrong password")

    start_passphrase_entry("Home")

    assert is_passphrase_waiting() is False
    assert passphrase_error() is None


def test_cancel_passphrase_entry_clears_waiting_and_error_too():
    start_passphrase_entry("Home")
    mark_passphrase_submitted()
    set_passphrase_error("wrong password")

    cancel_passphrase_entry()

    assert is_entering_passphrase() is False
    assert is_passphrase_waiting() is False
    assert passphrase_error() is None


def test_entering_passphrase_ssid_none_when_nothing_in_progress():
    assert entering_passphrase_ssid() is None


# ---------- pairing confirm: typing/browsing ----------

def test_not_confirming_pairing_initially():
    assert is_confirming_pairing() is False


def test_start_pairing_confirm_claims_it():
    request = object()

    start_pairing_confirm(request)

    assert is_confirming_pairing() is True
    assert connectivity_module._pairing_request is request
    assert current_pairing_request() is request


def test_cancel_pairing_confirm_clears_it():
    start_pairing_confirm(object())

    cancel_pairing_confirm()

    assert is_confirming_pairing() is False
    assert current_pairing_request() is None


# ---------- pairing confirm: waiting / error ----------

def test_not_waiting_or_erroring_right_after_start_pairing():
    start_pairing_confirm(object())

    assert is_pairing_waiting() is False
    assert pairing_error() is None


def test_mark_pairing_submitted_enters_waiting_state():
    start_pairing_confirm(object())

    mark_pairing_submitted()

    assert is_pairing_waiting() is True


def test_set_pairing_error_ends_waiting_and_records_the_message():
    start_pairing_confirm(object())
    mark_pairing_submitted()

    set_pairing_error("org.bluez.Error.AuthenticationFailed")

    assert is_pairing_waiting() is False
    assert pairing_error() == "org.bluez.Error.AuthenticationFailed"
    assert is_confirming_pairing() is True


def test_cancel_pairing_confirm_clears_waiting_and_error_too():
    start_pairing_confirm(object())
    mark_pairing_submitted()
    set_pairing_error("failed")

    cancel_pairing_confirm()

    assert is_confirming_pairing() is False
    assert is_pairing_waiting() is False
    assert pairing_error() is None
