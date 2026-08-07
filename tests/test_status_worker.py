"""Tests for status_worker.py — Domain/StatusWorker, VISION.md's R3.

Pending-tracking tests never call .start() (same reasoning
ConnectivityWorker's own tests used to state explicitly: never
starting the background thread means _run() never touches anything).
The no-silent-failure (last_error, None vs []) and action-dispatch
behavior needs the real poll loop running, so those tests do call
start()/stop() against fake domains with no real I/O — _wait_until
polls for the expected state instead of a fixed sleep, so the common
case resolves as soon as the worker's own loop actually gets there
(bounded below by its internal 0.2s per-iteration `time.sleep`, not by
this file's own guess at a "safe enough" delay) while still tolerating
a slow/loaded CI machine within the timeout, instead of being flaky
either way a fixed sleep would be.
"""

import time

from tuicc.status_worker import StatusWorker, Domain


def _domain(name, poll=None, actions=None, poll_interval=None):
    return Domain(name=name, poll=poll or (lambda: []), actions=actions or {}, poll_interval=poll_interval)


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------- pending tracking (no thread started) ----------

def test_request_action_marks_pending():
    worker = StatusWorker([_domain("wifi")])
    worker.request_action("wifi", "connect", "Home")
    assert worker.is_pending("wifi", "Home") is True


def test_pending_is_per_key():
    worker = StatusWorker([_domain("wifi")])
    worker.request_action("wifi", "connect", "Home")
    assert worker.is_pending("wifi", "Office") is False


def test_pending_is_per_domain():
    # Same key, different domain — must not collide (mirrors
    # ConnectivityWorker's old "wifi"/"bt" tuple-prefix distinction).
    worker = StatusWorker([_domain("wifi"), _domain("bluetooth")])
    worker.request_action("wifi", "connect", "AA")
    assert worker.is_pending("bluetooth", "AA") is False


def test_has_pending_reflects_any_request():
    worker = StatusWorker([_domain("wifi")])
    assert worker.has_pending() is False
    worker.request_action("wifi", "connect", "Home")
    assert worker.has_pending() is True


# ---------- get() / get_error(): initial state ----------

def test_get_is_none_before_first_poll():
    worker = StatusWorker([_domain("wifi")])
    assert worker.get("wifi") is None


def test_get_error_is_none_before_first_poll():
    # Not yet attempted is not the same as "attempted and failed" —
    # see modules/connectivity.py's _build_rows for why that
    # distinction matters (cold-start must not look like an error).
    worker = StatusWorker([_domain("wifi")])
    assert worker.get_error("wifi") is None


# ---------- no-silent-failure: poll success/failure ----------

def test_successful_poll_populates_snapshot_and_clears_error():
    worker = StatusWorker([_domain("wifi", poll=lambda: ["net1", "net2"])], poll_interval=0)
    worker.start()
    try:
        _wait_until(lambda: worker.get("wifi") is not None)
        assert worker.get("wifi") == ["net1", "net2"]
        assert worker.get_error("wifi") is None
    finally:
        worker.stop()


def test_failing_poll_sets_none_snapshot_and_captures_error():
    def _raise():
        raise RuntimeError("D-Bus unreachable")

    worker = StatusWorker([_domain("wifi", poll=_raise)], poll_interval=0)
    worker.start()
    try:
        _wait_until(lambda: worker.get_error("wifi") is not None)
        assert worker.get("wifi") is None
        assert worker.get_error("wifi") == "D-Bus unreachable"
    finally:
        worker.stop()


def test_empty_list_is_distinct_from_a_failed_poll():
    # The whole point of None vs [] — a domain that genuinely has
    # nothing to report is not an error.
    worker = StatusWorker([_domain("wifi", poll=lambda: [])], poll_interval=0)
    worker.start()
    try:
        _wait_until(lambda: worker.get("wifi") == [])
        assert worker.get("wifi") == []
        assert worker.get_error("wifi") is None
    finally:
        worker.stop()


def test_one_domains_poll_failure_does_not_affect_another():
    def _raise():
        raise RuntimeError("bluetoothctl timeout")

    worker = StatusWorker(
        [_domain("wifi", poll=lambda: ["net1"]), _domain("bluetooth", poll=_raise)],
        poll_interval=0,
    )
    worker.start()
    try:
        _wait_until(lambda: worker.get_error("bluetooth") is not None)
        assert worker.get("wifi") == ["net1"]
        assert worker.get_error("wifi") is None
        assert worker.get("bluetooth") is None
        assert worker.get_error("bluetooth") == "bluetoothctl timeout"
    finally:
        worker.stop()


# ---------- action dispatch ----------

def test_action_runs_the_registered_callable_with_its_arg():
    calls = []
    worker = StatusWorker(
        [_domain("wifi", actions={"connect": lambda ssid: calls.append(ssid)})],
        poll_interval=999,
    )
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: calls == ["Home"])
        assert calls == ["Home"]
    finally:
        worker.stop()


def test_action_clears_pending_once_processed():
    worker = StatusWorker(
        [_domain("wifi", actions={"connect": lambda ssid: None})],
        poll_interval=999,
    )
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: not worker.is_pending("wifi", "Home"))
        assert worker.is_pending("wifi", "Home") is False
    finally:
        worker.stop()


def test_action_exception_does_not_crash_the_worker_and_still_clears_pending():
    # An action failure must never crash the worker thread or leave a
    # pending spinner stuck forever, whether or not anyone's watching
    # get_action_error() for it (see the dedicated action-error tests
    # below for that part — this test is just "the thread survives").
    def _raise(arg):
        raise RuntimeError("connect failed")

    worker = StatusWorker(
        [_domain("wifi", actions={"connect": _raise})],
        poll_interval=999,
    )
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: not worker.is_pending("wifi", "Home"))
        assert worker.is_pending("wifi", "Home") is False
    finally:
        worker.stop()


def test_unregistered_action_name_is_a_noop_not_a_crash():
    worker = StatusWorker([_domain("wifi", actions={})], poll_interval=999)
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: not worker.is_pending("wifi", "Home"))  # must not raise / hang
        assert worker.is_pending("wifi", "Home") is False
    finally:
        worker.stop()


# ---------- get_action_error() ----------
# No direct tests existed for this before — found while touching this
# file for the per-domain poll_interval tests below, closing a real gap
# (the mechanism itself was built earlier, for the live gammastep bug,
# but only ever exercised indirectly through other tests).

def test_get_action_error_is_none_before_any_action():
    worker = StatusWorker([_domain("wifi")])
    assert worker.get_action_error("wifi") is None


def test_get_action_error_set_after_a_failing_action():
    def _raise(arg):
        raise RuntimeError("connect failed")

    worker = StatusWorker([_domain("wifi", actions={"connect": _raise})], poll_interval=999)
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: worker.get_action_error("wifi") is not None)
        assert worker.get_action_error("wifi") == "connect failed"
    finally:
        worker.stop()


def test_get_action_error_cleared_by_a_subsequent_successful_action():
    calls = {"n": 0}

    def _flaky(arg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connect failed")

    worker = StatusWorker([_domain("wifi", actions={"connect": _flaky})], poll_interval=999)
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: worker.get_action_error("wifi") is not None)

        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: worker.get_action_error("wifi") is None)
        assert worker.get_action_error("wifi") is None
    finally:
        worker.stop()


def test_get_action_error_is_per_domain():
    def _raise(arg):
        raise RuntimeError("connect failed")

    worker = StatusWorker(
        [_domain("wifi", actions={"connect": _raise}), _domain("bluetooth")],
        poll_interval=999,
    )
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: worker.get_action_error("wifi") is not None)
        assert worker.get_action_error("bluetooth") is None
    finally:
        worker.stop()


def test_get_action_error_survives_a_successful_poll_on_the_same_domain():
    # The core reasoning from status_worker.py's own module docstring:
    # _run() always re-polls every due domain in the same loop
    # iteration right after processing actions, so an action error
    # sharing a slot with poll's own _errors would get silently
    # clobbered by that very poll's own (likely successful) result
    # before any caller read it.
    def _raise(arg):
        raise RuntimeError("connect failed")

    worker = StatusWorker(
        [_domain("wifi", poll=lambda: ["net1"], actions={"connect": _raise})],
        poll_interval=0,  # always due, exercises the "clobbered by poll" risk on every tick
    )
    worker.start()
    try:
        worker.request_action("wifi", "connect", "Home")
        _wait_until(lambda: worker.get_action_error("wifi") is not None)
        time.sleep(0.5)  # several more poll ticks worth of runway
        assert worker.get_action_error("wifi") == "connect failed"
        assert worker.get_error("wifi") is None  # poll itself never failed
    finally:
        worker.stop()


# ---------- Domain.poll_interval: per-domain due checks ----------

def test_domain_with_no_poll_interval_uses_the_shared_default():
    worker = StatusWorker([_domain("wifi", poll=lambda: ["net1"])], poll_interval=999)
    worker.start()
    try:
        _wait_until(lambda: worker.get("wifi") is not None)  # the first poll always runs immediately
        assert worker.get("wifi") == ["net1"]
    finally:
        worker.stop()


def test_domain_with_a_short_poll_interval_refreshes_independently_of_the_shared_default():
    call_count = {"n": 0}

    def _poll():
        call_count["n"] += 1
        return [call_count["n"]]

    worker = StatusWorker(
        [
            _domain("wifi", poll=lambda: ["static"]),  # shared default (999s) — effectively never refreshes again
            _domain("media", poll=_poll, poll_interval=0.1),
        ],
        poll_interval=999,
    )
    worker.start()
    try:
        _wait_until(lambda: call_count["n"] >= 3, timeout=2.0)
        assert call_count["n"] >= 3  # "media" kept refreshing on its own short interval
        assert worker.get("wifi") == ["static"]  # "wifi" only got its one initial poll
    finally:
        worker.stop()


def test_acting_on_a_domain_forces_an_immediate_repoll_regardless_of_its_own_interval():
    poll_calls = []

    def _poll():
        poll_calls.append(len(poll_calls))
        return poll_calls

    worker = StatusWorker(
        [_domain("media", poll=_poll, actions={"noop": lambda arg: None}, poll_interval=999)],
    )
    worker.start()
    try:
        _wait_until(lambda: worker.get("media") is not None)
        calls_after_first_poll = len(poll_calls)

        worker.request_action("media", "noop", None)
        _wait_until(lambda: len(poll_calls) > calls_after_first_poll)
        assert len(poll_calls) > calls_after_first_poll  # re-polled despite the 999s interval
    finally:
        worker.stop()


# ---------- pause/resume ----------

def test_paused_worker_does_not_poll():
    # Asserting an absence — nothing to _wait_until for — so this is
    # the one bounded sleep left: a couple of the worker's own internal
    # 0.2s loop ticks worth of runway, long enough that a paused
    # worker's bug would show up reliably, short enough not to matter.
    worker = StatusWorker([_domain("wifi", poll=lambda: ["net1"])], poll_interval=0)
    worker.pause()
    worker.start()
    try:
        time.sleep(0.5)
        assert worker.get("wifi") is None
    finally:
        worker.stop()


def test_resumed_worker_polls_again():
    worker = StatusWorker([_domain("wifi", poll=lambda: ["net1"])], poll_interval=0)
    worker.pause()
    worker.start()
    try:
        time.sleep(0.3)
        assert worker.get("wifi") is None
        worker.resume()
        _wait_until(lambda: worker.get("wifi") is not None)
        assert worker.get("wifi") == ["net1"]
    finally:
        worker.stop()


# ---------- request_action: pending_key ----------
# Found live building R5's audio/ backend — set_volume(sink_id, percent)
# needs two pieces of data, but "is THIS SINK being adjusted" (the
# pending identity the UI actually cares about) has to stay just
# sink_id, not the whole (sink_id, percent) tuple, which would never
# match a second, different volume request for the same sink still in
# flight.

def test_pending_key_defaults_to_arg_when_omitted():
    # Backward compatible — every existing wifi/bluetooth call site
    # never passes pending_key, and must keep working exactly as before.
    worker = StatusWorker([_domain("wifi")])
    worker.request_action("wifi", "connect", "Home")
    assert worker.is_pending("wifi", "Home") is True


def test_pending_key_tracks_separately_from_arg_when_given():
    worker = StatusWorker([_domain("audio")])
    worker.request_action("audio", "set_volume", ("sink1", 80), pending_key="sink1")
    assert worker.is_pending("audio", "sink1") is True
    # The full tuple itself is not what's tracked.
    assert worker.is_pending("audio", ("sink1", 80)) is False


def test_action_with_pending_key_receives_the_full_arg_and_clears_correctly():
    calls = []
    worker = StatusWorker(
        [_domain("audio", actions={"set_volume": lambda arg: calls.append(arg)})],
        poll_interval=999,
    )
    worker.start()
    try:
        worker.request_action("audio", "set_volume", ("sink1", 80), pending_key="sink1")
        _wait_until(lambda: calls == [("sink1", 80)])
        assert calls == [("sink1", 80)]
        assert worker.is_pending("audio", "sink1") is False
    finally:
        worker.stop()
