"""Registry mapping backend names (from config) to backend classes —
one registry per contract, kept in one file since neither knows about
the other.
"""

from tuicc.connectivity.base import WifiBackend, BluetoothBackend
from tuicc.connectivity.iwd import IwdBackend
from tuicc.connectivity.bluez import BluezBackend

WIFI_BACKENDS = {
    "iwd": IwdBackend,
}

BLUETOOTH_BACKENDS = {
    "bluez": BluezBackend,
}


def build_wifi_backend(name: str) -> WifiBackend:
    if name not in WIFI_BACKENDS:
        raise ValueError(f"Unknown wifi backend: {name!r}. Available: {list(WIFI_BACKENDS.keys())}")
    return WIFI_BACKENDS[name]()


def build_bluetooth_backend(name: str) -> BluetoothBackend:
    if name not in BLUETOOTH_BACKENDS:
        raise ValueError(f"Unknown bluetooth backend: {name!r}. Available: {list(BLUETOOTH_BACKENDS.keys())}")
    return BLUETOOTH_BACKENDS[name]()
