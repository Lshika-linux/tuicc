"""Tests for push_worker.py — PushDomain/PushWorker, the event-driven
counterpart to status_worker.py's StatusWorker (see that module's own
docstring for why it's a separate class rather than a mode on
StatusWorker).

Fake domains use a controllable threading.Event ("trigger") instead of
a real kernel/subprocess event source, so tests stay deterministic —
same reasoning test_status_worker.py's own fake Domains use plain
lambdas instead of real subprocess calls. _wait_until mirrors that
file's own helper exactly.
"""

import threading
import time

from tuicc.push_worker import PushDomain, PushWorker


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _triggerable_watch(trigger: threading.Event):
    """A fake watch() — yields once every time `trigger` is set (then
    clears it), returns once stop_event is set. wait(timeout=...) keeps
    this from ever blocking forever even if a test forgets to trigger
    or stop it."""
    def watch(stop_event):
        while not stop_event.is_set():
            if trigger.wait(timeout=0.02):
                trigger.clear()
                if stop_event.is_set():
                    return
                yield
    return watch


# ---------- initial refresh ----------

def test_start_refreshes_immediately_without_waiting_for_an_event():
    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()),
                         refresh=lambda: {"percent": 50})
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get("battery") == {"percent": 50})
    finally:
        worker.stop()


def test_initial_refresh_error_surfaces_before_any_event():
    def _raise():
        raise RuntimeError("permission denied")

    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()), refresh=_raise)
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get_error("battery") == "permission denied")
        assert worker.get("battery") is None
    finally:
        worker.stop()


# ---------- reacting to a detected change ----------

def test_yielded_change_triggers_a_refresh():
    trigger = threading.Event()
    call_count = {"n": 0}

    def refresh():
        call_count["n"] += 1
        return {"percent": call_count["n"]}

    domain = PushDomain(name="battery", watch=_triggerable_watch(trigger), refresh=refresh)
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get("battery") == {"percent": 1})  # initial refresh
        trigger.set()
        assert _wait_until(lambda: worker.get("battery") == {"percent": 2})
        trigger.set()
        assert _wait_until(lambda: worker.get("battery") == {"percent": 3})
    finally:
        worker.stop()


def test_refresh_error_after_a_change_surfaces_and_clears_snapshot():
    trigger = threading.Event()
    state = {"fail": False}

    def refresh():
        if state["fail"]:
            raise RuntimeError("read failed")
        return {"percent": 10}

    domain = PushDomain(name="battery", watch=_triggerable_watch(trigger), refresh=refresh)
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get("battery") == {"percent": 10})
        state["fail"] = True
        trigger.set()
        assert _wait_until(lambda: worker.get_error("battery") == "read failed")
        assert worker.get("battery") is None
    finally:
        worker.stop()


def test_watch_itself_raising_surfaces_as_an_error():
    def broken_watch(stop_event):
        raise RuntimeError("watch mechanism broke")
        yield  # pragma: no cover — makes this a generator function

    domain = PushDomain(name="battery", watch=broken_watch, refresh=lambda: {"percent": 1})
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get_error("battery") == "watch mechanism broke")
    finally:
        worker.stop()


# ---------- pause/resume ----------

def test_paused_consumes_the_event_but_skips_the_refresh():
    trigger = threading.Event()
    call_count = {"n": 0}

    def refresh():
        call_count["n"] += 1
        return call_count["n"]

    domain = PushDomain(name="battery", watch=_triggerable_watch(trigger), refresh=refresh)
    worker = PushWorker([domain])
    worker.start()
    try:
        assert _wait_until(lambda: worker.get("battery") == 1)  # initial refresh, unpaused
        worker.pause()
        trigger.set()
        time.sleep(0.1)  # give the (paused) watch loop time to have consumed the event if it were going to refresh
        assert worker.get("battery") == 1  # unchanged — the pause skipped the refresh
        worker.resume()
        trigger.set()
        assert _wait_until(lambda: worker.get("battery") == 2)
    finally:
        worker.stop()


# ---------- request_action ----------

def test_request_action_marks_and_clears_pending():
    # request_action runs each action on its own short-lived thread
    # (see PushWorker.request_action's own docstring for why — no
    # shared queue to guarantee a "definitely still pending" window the
    # way StatusWorker's own queued model gets for free), so a trivial/
    # instant action can legitimately already be done by the time this
    # test could observe is_pending()==True — only the EVENTUAL
    # "cleared, and it ran" outcome is something to assert on.
    calls = []
    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()),
                         refresh=lambda: None, actions={"noop": calls.append})
    worker = PushWorker([domain])
    worker.request_action("battery", "noop", "x")
    assert _wait_until(lambda: not worker.is_pending("battery", "x"))
    assert calls == ["x"]


def test_request_action_error_surfaces_in_get_action_error():
    def _raise(arg):
        raise RuntimeError("action failed")

    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()),
                         refresh=lambda: None, actions={"boom": _raise})
    worker = PushWorker([domain])
    worker.request_action("battery", "boom")
    assert _wait_until(lambda: worker.get_action_error("battery") == "action failed")


def test_request_action_refreshes_afterward():
    call_count = {"n": 0}

    def refresh():
        call_count["n"] += 1
        return call_count["n"]

    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()),
                         refresh=refresh, actions={"noop": lambda arg: None})
    worker = PushWorker([domain])
    worker.request_action("battery", "noop")
    assert _wait_until(lambda: call_count["n"] >= 1)


def test_has_pending():
    # Uses a deliberately slow action (blocks on its own Event) so the
    # "pending" window is long enough to reliably observe — same
    # raciness reasoning as test_request_action_marks_and_clears_pending
    # above.
    release = threading.Event()
    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()),
                         refresh=lambda: None,
                         actions={"slow": lambda arg: release.wait(timeout=1.0)})
    worker = PushWorker([domain])
    assert worker.has_pending() is False
    worker.request_action("battery", "slow")
    assert _wait_until(lambda: worker.has_pending() is True, timeout=0.5)
    release.set()
    assert _wait_until(lambda: worker.has_pending() is False)


# ---------- domain_names ----------

def test_domain_names():
    domain = PushDomain(name="battery", watch=_triggerable_watch(threading.Event()), refresh=lambda: None)
    worker = PushWorker([domain])
    assert worker.domain_names() == {"battery"}
