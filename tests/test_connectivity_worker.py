"""Tests for ConnectivityWorker's pending-request tracking — request_*
and is_*_pending are plain state mutations on self._pending, testable
without ever starting the background thread (never calling .start()
means _run() never touches the backend at all).
"""

from tuicc.connectivity.worker import ConnectivityWorker


def _worker():
    return ConnectivityWorker(wifi_backend=None, bluetooth_backend=None)


def test_wifi_connect_marks_pending():
    worker = _worker()
    worker.request_wifi_connect("Home")
    assert worker.is_wifi_pending("Home") is True


def test_wifi_disconnect_marks_pending():
    worker = _worker()
    worker.request_wifi_disconnect("Home")
    assert worker.is_wifi_pending("Home") is True


def test_wifi_pending_is_per_ssid():
    worker = _worker()
    worker.request_wifi_connect("Home")
    assert worker.is_wifi_pending("Office") is False


def test_bluetooth_connect_marks_pending():
    worker = _worker()
    worker.request_bluetooth_connect("AA:BB")
    assert worker.is_bluetooth_pending("AA:BB") is True


def test_bluetooth_disconnect_marks_pending():
    worker = _worker()
    worker.request_bluetooth_disconnect("AA:BB")
    assert worker.is_bluetooth_pending("AA:BB") is True


def test_has_pending_reflects_any_request():
    worker = _worker()
    assert worker.has_pending() is False
    worker.request_wifi_connect("Home")
    assert worker.has_pending() is True