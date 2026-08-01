"""Tests for modules/connectivity.py's ACTION_HANDLERS — handle_wifi and
handle_bluetooth — with a fake ConnectivityWorker so no real D-Bus/
bluetoothctl I/O is needed.
"""

from types import SimpleNamespace

from tuicc.actions import ActionContext
from tuicc.connectivity.model import WifiNetwork, BluetoothDevice
from tuicc.modules.connectivity import handle_wifi, handle_bluetooth


class _FakeConnectivity:
    def __init__(self, wifi_networks=None, bluetooth_devices=None):
        self._wifi_networks = wifi_networks or []
        self._bluetooth_devices = bluetooth_devices or []
        self.wifi_connect_calls = []
        self.wifi_disconnect_calls = []
        self.bluetooth_connect_calls = []
        self.bluetooth_disconnect_calls = []

    def get_wifi_networks(self):
        return self._wifi_networks

    def get_bluetooth_devices(self):
        return self._bluetooth_devices

    def request_wifi_connect(self, ssid):
        self.wifi_connect_calls.append(ssid)

    def request_wifi_disconnect(self, ssid):
        self.wifi_disconnect_calls.append(ssid)

    def request_bluetooth_connect(self, device_id):
        self.bluetooth_connect_calls.append(device_id)

    def request_bluetooth_disconnect(self, device_id):
        self.bluetooth_disconnect_calls.append(device_id)


# ---------- handle_wifi ----------

def test_handle_wifi_connects_when_not_connected():
    connectivity = _FakeConnectivity(wifi_networks=[WifiNetwork(ssid="Home", connected=False)])
    ctx = ActionContext(provider=None, connectivity=connectivity)
    item = SimpleNamespace(focus_target="Home")

    handle_wifi(ctx, item, cfg=None)

    assert connectivity.wifi_connect_calls == ["Home"]
    assert connectivity.wifi_disconnect_calls == []


def test_handle_wifi_disconnects_when_already_connected():
    # Mirrors handle_bluetooth's toggle behavior below — selecting an
    # already-connected network and pressing confirm should disconnect
    # it, not silently re-issue a connect.
    connectivity = _FakeConnectivity(wifi_networks=[WifiNetwork(ssid="Home", connected=True)])
    ctx = ActionContext(provider=None, connectivity=connectivity)
    item = SimpleNamespace(focus_target="Home")

    handle_wifi(ctx, item, cfg=None)

    assert connectivity.wifi_disconnect_calls == ["Home"]
    assert connectivity.wifi_connect_calls == []


# ---------- handle_bluetooth ----------

def test_handle_bluetooth_connects_when_not_connected():
    connectivity = _FakeConnectivity(bluetooth_devices=[BluetoothDevice(id="AA", name="Speaker", connected=False)])
    ctx = ActionContext(provider=None, connectivity=connectivity)
    item = SimpleNamespace(focus_target="AA")

    handle_bluetooth(ctx, item, cfg=None)

    assert connectivity.bluetooth_connect_calls == ["AA"]
    assert connectivity.bluetooth_disconnect_calls == []


def test_handle_bluetooth_disconnects_when_already_connected():
    connectivity = _FakeConnectivity(bluetooth_devices=[BluetoothDevice(id="AA", name="Speaker", connected=True)])
    ctx = ActionContext(provider=None, connectivity=connectivity)
    item = SimpleNamespace(focus_target="AA")

    handle_bluetooth(ctx, item, cfg=None)

    assert connectivity.bluetooth_disconnect_calls == ["AA"]
    assert connectivity.bluetooth_connect_calls == []