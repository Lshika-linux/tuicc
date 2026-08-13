"""Tests for the iwd and bluez connectivity backends' pure logic — both
talk to their daemon directly over D-Bus now (no CLI text to parse for
either), so what's tested here is the pure object-tree-parsing logic,
separate from the D-Bus I/O itself.
"""

import pytest

import tuicc.connectivity.bluez as bluez_module
import tuicc.connectivity.iwd as iwd_module
from tuicc.connectivity.iwd import IwdBackend, find_station_path_in_objects, _signal_to_percent, _connect_succeeded
from tuicc.connectivity.bluez import (
    BluezBackend,
    find_adapter_path_in_objects,
    find_device_path_in_objects,
    find_devices_in_objects,
)


# ---------- iwd: find_station_path_in_objects ----------

def test_find_station_path_finds_matching_object():
    objects = {
        "/net/connman/iwd/0": {"net.connman.iwd.Adapter": {}},
        "/net/connman/iwd/0/4": {
            "net.connman.iwd.Device": {},
            "net.connman.iwd.Station": {},
        },
    }

    assert find_station_path_in_objects(objects) == "/net/connman/iwd/0/4"


def test_find_station_path_no_station_returns_none():
    objects = {
        "/net/connman/iwd/0": {"net.connman.iwd.Adapter": {}},
    }

    assert find_station_path_in_objects(objects) is None


def test_find_station_path_empty_objects_returns_none():
    assert find_station_path_in_objects({}) is None


# ---------- iwd: _signal_to_percent ----------

def test_signal_to_percent_strong_signal():
    # -51.00 dBm, centi-dBm as returned by GetOrderedNetworks
    assert _signal_to_percent(-5100) > 50


def test_signal_to_percent_weak_signal():
    assert _signal_to_percent(-8300) < 30


def test_signal_to_percent_clamped_to_0_100():
    assert _signal_to_percent(-20000) == 0  # absurdly weak, must not go negative
    assert _signal_to_percent(0) == 100      # absurdly strong, must not exceed 100


def test_signal_to_percent_exact_midpoint():
    # Per the function's own documented spec (-30 dBm = 100%, -90 dBm =
    # 0%, linear in between): -60 dBm is exactly halfway, so this must
    # read 50%. Derived independently from the spec, not from the
    # implementation's own formula.
    assert _signal_to_percent(-6000) == 50


# ---------- iwd: _connect_succeeded ----------
# See CLAUDE/NOTES/design-decisions.md#iwd-connect-false-success —
# Network.Connect() itself returns cleanly (no D-Bus error) even when
# the passphrase was wrong; ConnectedNetwork simply never becomes the
# attempted network.

def test_connect_succeeded_when_connected_network_matches():
    station_props = {"ConnectedNetwork": ("o", "/net/connman/iwd/0/4/target_psk")}

    assert _connect_succeeded(station_props, "/net/connman/iwd/0/4/target_psk") is True


def test_connect_succeeded_false_when_connected_network_is_a_different_one():
    # The exact wrong-passphrase case — Station stayed on (or reverted
    # to) whatever it was connected to before, not the attempted one.
    station_props = {"ConnectedNetwork": ("o", "/net/connman/iwd/0/4/other_psk")}

    assert _connect_succeeded(station_props, "/net/connman/iwd/0/4/target_psk") is False


def test_connect_succeeded_false_when_connected_network_absent():
    # Not connected to anything at all after the attempt.
    station_props = {}

    assert _connect_succeeded(station_props, "/net/connman/iwd/0/4/target_psk") is False


# ---------- bluez: find_devices_in_objects ----------
# Fixture trimmed from a real `org.bluez` GetManagedObjects() reply
# captured live against this machine's bluez (GATT service/characteristic
# sub-objects stripped out — find_devices_in_objects only looks at
# org.bluez.Device1/org.bluez.Battery1, same "real, live-captured
# fixture" discipline R5's mpris.py fixture used). The Battery1 entry
# on the second device is NOT from that live capture — no currently-
# connected device on this machine exposes one — it's synthesized
# from bluez's documented org.bluez.Battery1 shape (a single
# `Percentage` byte property) to exercise that code path.

BLUEZ_MANAGED_OBJECTS = {
    "/org/bluez": {
        "org.bluez.AgentManager1": {},
        "org.bluez.ProfileManager1": {},
    },
    "/org/bluez/hci0": {
        "org.bluez.Adapter1": {
            "Address": ("s", "D0:C6:37:61:24:D8"),
            "Alias": ("s", "node1"),
            "Powered": ("b", True),
        },
    },
    "/org/bluez/hci0/dev_00_00_00_06_5A_52": {
        "org.bluez.Device1": {
            "Adapter": ("o", "/org/bluez/hci0"),
            "Address": ("s", "00:00:00:06:5A:52"),
            "AddressType": ("s", "public"),
            "Alias": ("s", "SLRB 30 A1"),
            "Name": ("s", "SLRB 30 A1"),
            "Icon": ("s", "audio-headset"),
            "Paired": ("b", True),
            "Connected": ("b", False),
            "Trusted": ("b", False),
            "Blocked": ("b", False),
        },
    },
    "/org/bluez/hci0/dev_1C_6E_4C_9C_D0_41": {
        "org.bluez.Device1": {
            "Adapter": ("o", "/org/bluez/hci0"),
            "Address": ("s", "1C:6E:4C:9C:D0:41"),
            "AddressType": ("s", "public"),
            "Alias": ("s", "MAJOR IV"),
            "Name": ("s", "MAJOR IV"),
            "Icon": ("s", "audio-headphones"),
            "Paired": ("b", True),
            "Connected": ("b", True),
            "Trusted": ("b", True),
            "Blocked": ("b", False),
        },
        "org.bluez.Battery1": {
            "Percentage": ("y", 60),
        },
    },
    "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
        "org.bluez.Device1": {
            "Adapter": ("o", "/org/bluez/hci0"),
            "Address": ("s", "AA:BB:CC:DD:EE:FF"),
            "AddressType": ("s", "random"),
            "Alias": ("s", "AA-BB-CC-DD-EE-FF"),
            "Paired": ("b", False),
            "Connected": ("b", False),
            "Trusted": ("b", False),
            "Blocked": ("b", False),
        },
    },
}


def test_find_devices_returns_one_per_device1_object():
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)

    assert {d.id for d in devices} == {
        "00:00:00:06:5A:52", "1C:6E:4C:9C:D0:41", "AA:BB:CC:DD:EE:FF",
    }


def test_find_devices_ignores_non_device_objects():
    # /org/bluez and /org/bluez/hci0 have no org.bluez.Device1 interface
    # at all — must not show up as (broken) devices.
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)

    assert all(d.id != "D0:C6:37:61:24:D8" for d in devices)


def test_find_devices_includes_unpaired_devices():
    # The whole point of the D-Bus rewrite (see bluez.py's own module
    # docstring): unlike the old bluetoothctl-based `devices Paired`
    # call, an unpaired device must still show up, marked as such.
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    unpaired = next(d for d in devices if d.id == "AA:BB:CC:DD:EE:FF")

    assert unpaired.paired is False
    assert unpaired.connected is False


def test_find_devices_reads_paired_connected_correctly():
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    major_iv = next(d for d in devices if d.id == "1C:6E:4C:9C:D0:41")

    assert major_iv.name == "MAJOR IV"
    assert major_iv.paired is True
    assert major_iv.connected is True


def test_find_devices_reads_hover_preview_fields():
    # Trusted/Blocked/Icon/AddressType — modules/connectivity.py's
    # hover-preview info, zero extra D-Bus round trips (already in the
    # same GetManagedObjects reply, see _device_from_props' own
    # docstring).
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    major_iv = next(d for d in devices if d.id == "1C:6E:4C:9C:D0:41")

    assert major_iv.trusted is True
    assert major_iv.blocked is False
    assert major_iv.icon == "audio-headphones"
    assert major_iv.address_type == "public"


def test_find_devices_icon_and_rssi_none_when_absent():
    # AA:BB:CC:DD:EE:FF's fixture has no Icon/RSSI key at all — bluez
    # only reports them once it actually has a value (a class-based
    # guess, or an active scan sighting respectively).
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    unknown = next(d for d in devices if d.id == "AA:BB:CC:DD:EE:FF")

    assert unknown.icon is None
    assert unknown.rssi is None


def test_find_devices_reads_battery_when_battery1_present():
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    major_iv = next(d for d in devices if d.id == "1C:6E:4C:9C:D0:41")

    assert major_iv.battery == 60


def test_find_devices_battery_none_when_battery1_absent():
    devices = find_devices_in_objects(BLUEZ_MANAGED_OBJECTS)
    slrb = next(d for d in devices if d.id == "00:00:00:06:5A:52")

    assert slrb.battery is None


def test_find_devices_empty_objects_returns_empty_list():
    assert find_devices_in_objects({}) == []


# ---------- bluez: find_device_path_in_objects ----------

def test_find_device_path_matches_by_address():
    path = find_device_path_in_objects(BLUEZ_MANAGED_OBJECTS, "1C:6E:4C:9C:D0:41")

    assert path == "/org/bluez/hci0/dev_1C_6E_4C_9C_D0_41"


def test_find_device_path_no_match_returns_none():
    assert find_device_path_in_objects(BLUEZ_MANAGED_OBJECTS, "FF:FF:FF:FF:FF:FF") is None


# ---------- bluez: find_adapter_path_in_objects ----------

def test_find_adapter_path_finds_matching_object():
    assert find_adapter_path_in_objects(BLUEZ_MANAGED_OBJECTS) == "/org/bluez/hci0"


def test_find_adapter_path_no_adapter_returns_none():
    assert find_adapter_path_in_objects({"/org/bluez": {"org.bluez.AgentManager1": {}}}) is None


# ---------- no-silent-failure: real exceptions must propagate ----------
# VISION.md's R3: both backends used to have their OWN internal
# except-and-hide-behind-[] before status_worker.StatusWorker's poll
# wrapper ever got a chance to see a failure, making its last_error
# mechanism unreachable for these two domains specifically. bluez.py's
# D-Bus rewrite (R4) inherits iwd.py's own "no internal try/except"
# discipline rather than reintroducing bluetoothctl's old one — this
# guards against that creeping back in for bluez.py too.

def test_iwd_get_networks_propagates_dbus_connection_failure(monkeypatch):
    def _raise(**kwargs):
        raise ConnectionError("SYSTEM bus unreachable")

    monkeypatch.setattr(iwd_module, "open_dbus_connection", _raise)

    with pytest.raises(ConnectionError):
        IwdBackend().get_networks()


def test_bluez_get_devices_propagates_dbus_connection_failure(monkeypatch):
    def _raise(**kwargs):
        raise ConnectionError("SYSTEM bus unreachable")

    monkeypatch.setattr(bluez_module, "open_dbus_connection", _raise)

    with pytest.raises(ConnectionError):
        BluezBackend().get_devices()


def test_iwd_is_scanning_propagates_dbus_connection_failure(monkeypatch):
    def _raise(**kwargs):
        raise ConnectionError("SYSTEM bus unreachable")

    monkeypatch.setattr(iwd_module, "open_dbus_connection", _raise)

    with pytest.raises(ConnectionError):
        IwdBackend().is_scanning()


def test_bluez_is_discovering_propagates_dbus_connection_failure(monkeypatch):
    def _raise(**kwargs):
        raise ConnectionError("SYSTEM bus unreachable")

    monkeypatch.setattr(bluez_module, "open_dbus_connection", _raise)

    with pytest.raises(ConnectionError):
        BluezBackend().is_discovering()
