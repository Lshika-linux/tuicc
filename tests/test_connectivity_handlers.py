"""Tests for modules/connectivity.py's ACTION_HANDLERS — handle_wifi and
handle_bluetooth — with a fake StatusWorker so no real D-Bus/
bluetoothctl I/O is needed.
"""

from types import SimpleNamespace

from tuicc.actions import ActionContext
from tuicc.connectivity.model import WifiNetwork, BluetoothDevice
from tuicc.modules.connectivity import handle_wifi, handle_bluetooth, handle_wifi_scan, handle_bluetooth_discover


class _FakeConnectivity:
    def __init__(self, wifi_networks=None, bluetooth_devices=None):
        self._snapshots = {"wifi": wifi_networks or [], "bluetooth": bluetooth_devices or []}
        self.wifi_connect_calls = []
        self.wifi_disconnect_calls = []
        self.bluetooth_connect_calls = []
        self.bluetooth_disconnect_calls = []
        self.wifi_scan_calls = []
        self.bluetooth_discover_calls = []

    def get(self, domain_name):
        return self._snapshots[domain_name]

    def request_action(self, domain_name, action_name, arg):
        calls = {
            ("wifi", "connect"): self.wifi_connect_calls,
            ("wifi", "disconnect"): self.wifi_disconnect_calls,
            ("wifi", "scan"): self.wifi_scan_calls,
            ("bluetooth", "connect"): self.bluetooth_connect_calls,
            ("bluetooth", "disconnect"): self.bluetooth_disconnect_calls,
            ("bluetooth", "discover"): self.bluetooth_discover_calls,
        }[(domain_name, action_name)]
        calls.append(arg)


# ---------- handle_wifi ----------

def test_handle_wifi_connects_when_not_connected():
    connectivity = _FakeConnectivity(wifi_networks=[WifiNetwork(ssid="Home", connected=False)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target="Home")

    handle_wifi(ctx, item, cfg=None)

    assert connectivity.wifi_connect_calls == ["Home"]
    assert connectivity.wifi_disconnect_calls == []


def test_handle_wifi_disconnects_when_already_connected():
    # Mirrors handle_bluetooth's toggle behavior below — selecting an
    # already-connected network and pressing confirm should disconnect
    # it, not silently re-issue a connect.
    connectivity = _FakeConnectivity(wifi_networks=[WifiNetwork(ssid="Home", connected=True)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target="Home")

    handle_wifi(ctx, item, cfg=None)

    assert connectivity.wifi_disconnect_calls == ["Home"]
    assert connectivity.wifi_connect_calls == []


# ---------- handle_bluetooth ----------

def test_handle_bluetooth_connects_when_not_connected():
    connectivity = _FakeConnectivity(bluetooth_devices=[BluetoothDevice(id="AA", name="Speaker", connected=False)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target="AA")

    handle_bluetooth(ctx, item, cfg=None)

    assert connectivity.bluetooth_connect_calls == ["AA"]
    assert connectivity.bluetooth_disconnect_calls == []


def test_handle_bluetooth_disconnects_when_already_connected():
    connectivity = _FakeConnectivity(bluetooth_devices=[BluetoothDevice(id="AA", name="Speaker", connected=True)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target="AA")

    handle_bluetooth(ctx, item, cfg=None)

    assert connectivity.bluetooth_disconnect_calls == ["AA"]
    assert connectivity.bluetooth_connect_calls == []


# ---------- handle_wifi_scan / handle_bluetooth_discover ----------

def test_handle_wifi_scan_requests_scan_action():
    connectivity = _FakeConnectivity()
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    should_dismiss, pending = handle_wifi_scan(ctx, item, cfg=None)

    assert connectivity.wifi_scan_calls == [None]
    assert (should_dismiss, pending) == (False, None)


def test_handle_bluetooth_discover_requests_discover_action():
    connectivity = _FakeConnectivity()
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    should_dismiss, pending = handle_bluetooth_discover(ctx, item, cfg=None)

    assert connectivity.bluetooth_discover_calls == [None]
    assert (should_dismiss, pending) == (False, None)