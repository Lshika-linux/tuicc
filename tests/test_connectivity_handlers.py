"""Tests for modules/connectivity.py's ACTION_HANDLERS — handle_wifi_header
and handle_bt_header, the level-1 header handlers that enter level-2
browsing (see connectivity.py's own "level-2 browsing" section
docstring) — with a fake StatusWorker so no real D-Bus/bluetoothctl I/O
is needed. toggle_wifi/toggle_bluetooth (the connect/disconnect logic
these used to inline before being extracted for level-2's own direct-
call use) are tested in test_connectivity_module.py instead, alongside
next_browsing_selection — this file stays scoped to the dispatch_action
ACTION_HANDLERS contract specifically.

Module-level browsing state (_browsing_section) is reset by hand at
the top of each test that touches it, same "no autouse fixture"
convention test_media_module.py's own docstring documents for
_expanded_bus_name.
"""

from types import SimpleNamespace

from tuicc.actions import ActionContext
from tuicc.connectivity.model import WifiNetwork, BluetoothDevice
from tuicc.modules.connectivity import handle_wifi_header, handle_bt_header, stop_browsing, is_browsing, browsing_section


class _FakeConnectivity:
    def __init__(self, wifi_networks=None, bluetooth_devices=None):
        self._snapshots = {"wifi": wifi_networks or [], "bluetooth": bluetooth_devices or []}

    def get(self, domain_name):
        return self._snapshots[domain_name]


# ---------- handle_wifi_header ----------

def test_handle_wifi_header_enters_browsing():
    stop_browsing()
    connectivity = _FakeConnectivity(wifi_networks=[WifiNetwork(ssid="Home", connected=False)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    should_dismiss, pending = handle_wifi_header(ctx, item, cfg=None)

    assert is_browsing() is True
    assert browsing_section() == "wifi"
    assert (should_dismiss, pending) == (False, None)


def test_handle_wifi_header_reselects_the_first_network():
    stop_browsing()
    connectivity = _FakeConnectivity(wifi_networks=[
        WifiNetwork(ssid="Home", connected=False), WifiNetwork(ssid="Office", connected=False),
    ])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    handle_wifi_header(ctx, item, cfg=None)

    assert ctx.reselect_item_id == "connectivity:wifi:Home"


def test_handle_wifi_header_reselects_the_empty_placeholder_when_no_networks():
    # Found live: leaving reselect_item_id unset here used to point
    # selection at the now-unreachable header id the instant browsing
    # started, which frame_update.py's stale-selection recovery reacted
    # to by silently reassigning selection to an unrelated module —
    # see _empty_browsing_nav_item()'s own docstring for the full
    # incident. The placeholder id gives it something real to point at
    # instead.
    stop_browsing()
    connectivity = _FakeConnectivity(wifi_networks=[])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    handle_wifi_header(ctx, item, cfg=None)

    assert is_browsing() is True  # still enters browsing — e.g. to press the scan key
    assert ctx.reselect_item_id == "connectivity:wifi:empty"


# ---------- handle_bt_header ----------

def test_handle_bt_header_enters_browsing():
    stop_browsing()
    connectivity = _FakeConnectivity(bluetooth_devices=[BluetoothDevice(id="AA", name="Speaker", connected=False)])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    should_dismiss, pending = handle_bt_header(ctx, item, cfg=None)

    assert is_browsing() is True
    assert browsing_section() == "bluetooth"
    assert (should_dismiss, pending) == (False, None)


def test_handle_bt_header_reselects_the_first_device():
    stop_browsing()
    connectivity = _FakeConnectivity(bluetooth_devices=[
        BluetoothDevice(id="AA", name="A", connected=False), BluetoothDevice(id="BB", name="B", connected=False),
    ])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    handle_bt_header(ctx, item, cfg=None)

    assert ctx.reselect_item_id == "connectivity:bt:AA"


def test_handle_bt_header_reselects_the_empty_placeholder_when_no_devices():
    stop_browsing()
    connectivity = _FakeConnectivity(bluetooth_devices=[])
    ctx = ActionContext(provider=None, status=connectivity)
    item = SimpleNamespace(focus_target=None)

    handle_bt_header(ctx, item, cfg=None)

    assert is_browsing() is True
    assert ctx.reselect_item_id == "connectivity:bt:empty"
