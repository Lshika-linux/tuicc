"""Registry mapping backend names (from config) to backend classes —
one registry per contract, kept in one file since neither knows about
the other.

WIFI_AGENTS is a fourth registry alongside the three backend ones —
NOT a backend contract, but the same "one dict, one build_*() lookup"
shape, for the same reason: a wifi *agent* is 1:1 with a wifi
*backend* of the same protocol, mutually exclusive by config, exactly
like WifiBackend itself. See base.py's WifiAgent docstring for why
this doesn't extend to bluetooth (BluezAgent has no second
implementation to register alongside).
"""

from tuicc.connectivity.base import WifiBackend, WifiAgent, BluetoothBackend
from tuicc.connectivity.iwd import IwdBackend
from tuicc.connectivity.iwd_agent import IwdAgent
from tuicc.connectivity.bluez import BluezBackend

WIFI_BACKENDS = {
    "iwd": IwdBackend,
}

WIFI_AGENTS = {
    "iwd": IwdAgent,
}

BLUETOOTH_BACKENDS = {
    "bluez": BluezBackend,
}


def build_wifi_backend(name: str) -> WifiBackend:
    if name not in WIFI_BACKENDS:
        raise ValueError(f"Unknown wifi backend: {name!r}. Available: {list(WIFI_BACKENDS.keys())}")
    return WIFI_BACKENDS[name]()


def build_wifi_agent(name: str) -> WifiAgent:
    if name not in WIFI_AGENTS:
        raise ValueError(f"Unknown wifi backend: {name!r}. Available: {list(WIFI_AGENTS.keys())}")
    return WIFI_AGENTS[name]()


def build_bluetooth_backend(name: str) -> BluetoothBackend:
    if name not in BLUETOOTH_BACKENDS:
        raise ValueError(f"Unknown bluetooth backend: {name!r}. Available: {list(BLUETOOTH_BACKENDS.keys())}")
    return BLUETOOTH_BACKENDS[name]()
