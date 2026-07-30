"""Shared data model for wifi and bluetooth backends.

Two independent shapes, not one merged state — a wifi backend and a
bluetooth backend are chosen independently in config and don't know
about each other, so there's no natural single "ConnectivityState" to
wrap them in.
"""

from dataclasses import dataclass


@dataclass
class WifiNetwork:
    ssid: str
    connected: bool
    signal: int | None = None  # 0-100, None if the backend can't report it


@dataclass
class BluetoothDevice:
    id: str
    name: str
    connected: bool
    battery: int | None = None  # 0-100, None if the device doesn't report it
