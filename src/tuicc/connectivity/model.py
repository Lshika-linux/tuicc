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
    known: bool = True  # False for a freshly-scanned network never connected to before
    # The rest are for modules/connectivity.py's hover-preview (VISION.md's
    # R4 follow-up) — each backend fills these in from whatever its own
    # protocol calls the same concept, normalizing onto this one shared
    # shape at ITS OWN boundary rather than this model widening per
    # backend (see iwd.py's _build_wifi_network()/networkmanager.py's
    # own equivalent for how each one gets there — iwd's cost nothing
    # extra beyond what get_networks() already pulls per network;
    # NetworkManager's needs correlating a scanned AP against a
    # separately-stored connection profile by SSID, see networkmanager.py's
    # own module docstring for why that's a real, not just syntactic,
    # difference from iwd's model).
    security: str | None = None  # "open"/"psk"/"8021x"/"wep"/"sae"/"owe" — backend-normalized, None if unknown
    auto_connect: bool | None = None  # only meaningful when known=True
    hidden: bool | None = None  # only meaningful when known=True
    last_connected: str | None = None  # ISO8601 UTC string, backend-normalized (e.g. from NetworkManager's own epoch-seconds timestamp), or None


@dataclass
class BluetoothDevice:
    id: str
    name: str
    connected: bool
    battery: int | None = None  # 0-100, None if the device doesn't report it
    paired: bool = True  # False for a freshly-discovered, unpaired device
    # The rest are for modules/connectivity.py's hover-preview — zero
    # extra D-Bus round trips, bluez.py's GetManagedObjects() reply
    # already carries the full org.bluez.Device1 property set per
    # device; these were just never extracted into the dataclass before.
    trusted: bool = False
    blocked: bool = False
    icon: str | None = None  # bluez's own Device1.Icon, e.g. "audio-headphones" — None if bluez has no guess
    address_type: str | None = None  # "public" | "random"
    rssi: int | None = None  # signed dBm — only present while actively discovering, None otherwise
