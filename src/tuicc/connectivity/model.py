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
    # R4 follow-up) — cost nothing extra to fetch server-side beyond
    # what get_networks() already pulls per network (security is in
    # the exact same Properties.GetAll call known already comes from);
    # the KnownNetwork-only fields need one further GetAll, only for
    # networks that ARE known, see iwd.py's own comment at the call site.
    security: str | None = None  # iwd's own Network.Type value ("open"/"psk"/"8021x"/...), None if unknown
    auto_connect: bool | None = None  # only meaningful when known=True
    hidden: bool | None = None  # only meaningful when known=True
    last_connected: str | None = None  # ISO8601 UTC string (iwd's own KnownNetwork.LastConnectedTime), or None


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
