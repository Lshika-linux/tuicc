"""Tests for combined_status.py's CombinedStatus — pure routing logic,
tested against small fake pull/push worker doubles rather than real
StatusWorker/PushWorker instances (no threads, no timing, this class
owns no state of its own to race on).
"""

import pytest

from tuicc.combined_status import CombinedStatus


class _FakeWorker:
    """Implements just enough of StatusWorker/PushWorker's shared shape
    to test CombinedStatus's own dispatch — records every call it
    receives so tests can assert routing without needing real domains.
    """
    def __init__(self, names, has_pending_value=False):
        self._names = set(names)
        self.calls = []
        self.started = False
        self.stopped = False
        self.paused = False
        self._has_pending = has_pending_value

    def domain_names(self):
        return self._names

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def get(self, name):
        self.calls.append(("get", name))
        return f"value:{name}"

    def get_error(self, name):
        self.calls.append(("get_error", name))
        return f"error:{name}"

    def get_action_error(self, name):
        self.calls.append(("get_action_error", name))
        return f"action_error:{name}"

    def is_pending(self, name, key):
        self.calls.append(("is_pending", name, key))
        return True

    def has_pending(self):
        return self._has_pending

    def request_action(self, name, action, arg=None, pending_key=None):
        self.calls.append(("request_action", name, action, arg, pending_key))


def _combined(pull_names=("wifi",), push_names=("battery",), **kwargs):
    pull = _FakeWorker(pull_names, **kwargs.get("pull_kwargs", {}))
    push = _FakeWorker(push_names, **kwargs.get("push_kwargs", {}))
    return CombinedStatus(pull, push), pull, push


# ---------- construction ----------

def test_overlapping_domain_names_raises():
    pull = _FakeWorker(["battery"])
    push = _FakeWorker(["battery"])
    with pytest.raises(ValueError):
        CombinedStatus(pull, push)


def test_no_overlap_constructs_fine():
    combined, pull, push = _combined()
    assert combined is not None


# ---------- routing: get/get_error/get_action_error/is_pending ----------

def test_get_routes_to_the_owning_worker():
    combined, pull, push = _combined(pull_names=("wifi",), push_names=("battery",))
    assert combined.get("wifi") == "value:wifi"
    assert ("get", "wifi") in pull.calls
    assert combined.get("battery") == "value:battery"
    assert ("get", "battery") in push.calls


def test_get_error_routes_to_the_owning_worker():
    combined, pull, push = _combined()
    assert combined.get_error("battery") == "error:battery"
    assert ("get_error", "battery") in push.calls
    assert ("get_error", "battery") not in pull.calls


def test_get_action_error_routes_to_the_owning_worker():
    combined, pull, push = _combined()
    assert combined.get_action_error("wifi") == "action_error:wifi"
    assert ("get_action_error", "wifi") in pull.calls


def test_is_pending_routes_to_the_owning_worker():
    combined, pull, push = _combined()
    assert combined.is_pending("battery", "key") is True
    assert ("is_pending", "battery", "key") in push.calls
    assert push.calls  # only push touched, not pull
    assert not pull.calls


def test_request_action_routes_to_the_owning_worker():
    combined, pull, push = _combined()
    combined.request_action("wifi", "connect", "Home", pending_key="Home")
    assert ("request_action", "wifi", "connect", "Home", "Home") in pull.calls
    assert not push.calls


# ---------- has_pending: combines both ----------

def test_has_pending_true_if_either_worker_has_pending():
    pull = _FakeWorker(["wifi"], has_pending_value=True)
    push = _FakeWorker(["battery"], has_pending_value=False)
    combined = CombinedStatus(pull, push)
    assert combined.has_pending() is True


def test_has_pending_false_if_neither_has_pending():
    combined, pull, push = _combined()
    assert combined.has_pending() is False


# ---------- start/stop/pause/resume: both workers ----------

def test_start_starts_both_workers():
    combined, pull, push = _combined()
    combined.start()
    assert pull.started is True
    assert push.started is True


def test_stop_stops_both_workers():
    combined, pull, push = _combined()
    combined.stop()
    assert pull.stopped is True
    assert push.stopped is True


def test_pause_resume_both_workers():
    combined, pull, push = _combined()
    combined.pause()
    assert pull.paused is True
    assert push.paused is True
    combined.resume()
    assert pull.paused is False
    assert push.paused is False
