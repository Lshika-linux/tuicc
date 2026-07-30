"""Tests for the iwd and bluez connectivity backends' pure logic —
iwd talks to iwd directly over D-Bus (no CLI text to parse), so what's
tested here is the pure data-shaping logic, separate from the D-Bus
I/O itself. bluez still parses bluetoothctl's CLI text, so those tests
remain fixture-based like before.
"""

from tuicc.connectivity.iwd import find_station_path_in_objects, _signal_to_percent
from tuicc.connectivity.bluez import parse_paired_devices_output, parse_info_output


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


def test_signal_to_percent_equal_dbm_gives_equal_percent():
    assert _signal_to_percent(-8300) == _signal_to_percent(-8300)


# ---------- bluez: parse_paired_devices_output ----------

PAIRED_DEVICES_OUTPUT = "Device 1C:6E:4C:9C:D0:41 MAJOR IV\n"


def test_parse_paired_devices_finds_device():
    devices = parse_paired_devices_output(PAIRED_DEVICES_OUTPUT)
    assert devices == [("1C:6E:4C:9C:D0:41", "MAJOR IV")]


def test_parse_paired_devices_empty_output_returns_empty_list():
    assert parse_paired_devices_output("") == []


def test_parse_paired_devices_ignores_non_device_lines():
    noisy = "Some unrelated log line\nDevice AA:BB:CC:DD:EE:FF Speaker\n"
    devices = parse_paired_devices_output(noisy)
    assert devices == [("AA:BB:CC:DD:EE:FF", "Speaker")]


# ---------- bluez: parse_info_output ----------

BLUETOOTH_INFO_CONNECTED_WITH_BATTERY = """Device 1C:6E:4C:9C:D0:41 (public)
\tName: MAJOR IV
\tAlias: MAJOR IV
\tClass: 0x00240418 (2360344)
\tIcon: audio-headphones
\tPaired: yes
\tBonded: yes
\tTrusted: no
\tBlocked: no
\tConnected: yes
\tLegacyPairing: no
\tCablePairing: no
\tBattery Percentage: 0x3c (60)
"""

BLUETOOTH_INFO_DISCONNECTED_NO_BATTERY = """Device AA:BB:CC:DD:EE:FF (public)
\tName: Some Speaker
\tConnected: no
"""


def test_parse_info_connected_with_battery():
    info = parse_info_output(BLUETOOTH_INFO_CONNECTED_WITH_BATTERY)

    assert info["connected"] is True
    assert info["battery"] == 60


def test_parse_info_disconnected_no_battery_field():
    info = parse_info_output(BLUETOOTH_INFO_DISCONNECTED_NO_BATTERY)

    assert info["connected"] is False
    assert info["battery"] is None


def test_parse_info_empty_output_defaults_safely():
    info = parse_info_output("")

    assert info["connected"] is False
    assert info["battery"] is None
