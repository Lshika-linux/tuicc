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
class AdapterInfo:
    """The wifi adapter/device itself — genuinely new info, not a
    per-network concept, hence its own dataclass rather than more
    WifiNetwork fields. Fed to modules/connectivity.py's WiFi header
    hover-preview (see its own "level-2 browsing" section) — the
    natural existing "separate info panel" slot, matching how impala
    shows this in its own dedicated panel, without inventing a fourth
    mode_stack tier for something that's read-only.

    model/vendor/supported_modes are iwd-only in practice —
    NetworkManager's D-Bus API has no equivalent property for any of
    the three, so networkmanager.py's own get_adapter_info() leaves
    them None, same "degrade honestly, don't guess" precedent
    WifiNetwork.security's unknown-value fallback already sets.
    Frequency/live-negotiated-security of the CURRENT connection are
    NOT here — see ConnectionDiagnostics below instead. (An earlier
    version of this docstring claimed they already lived on the
    connected WifiNetwork object — wrong, checked live: WifiNetwork has
    no frequency field at all, and its own `security` is the network's
    generically-configured type, not what actually got negotiated this
    session — see ConnectionDiagnostics' own docstring for why those
    two can genuinely differ.)
    """
    name: str | None = None  # iwd Device.Name / NM's own interface name, e.g. "wlan0"
    address: str | None = None  # MAC
    model: str | None = None
    vendor: str | None = None
    supported_modes: list[str] | None = None
    mode: str | None = None  # "station"/"ap"/"ad-hoc"
    powered: bool | None = None
    state: str | None = None  # raw backend string ("connected"/"disconnected"/...), not normalized
    # ISO8601 UTC string, same backend-normalized shape as WifiNetwork.
    # last_connected — NetworkManager-only in practice (Device.Wireless.
    # LastScan), added specifically because is_scanning() alone turned
    # out to be a near-useless signal on real hardware: a rescan
    # completes fast enough that the boolean almost never reads True by
    # the time this app's own poll catches it (found live — see
    # modules/connectivity.py's guarantee_scan_blink() for the UI side
    # of the same discovery). iwd exposes no equivalent property —
    # stays None there, same honest-gap precedent model/vendor/
    # supported_modes already set the other way around above.
    last_scan: str | None = None


@dataclass
class ConnectionDiagnostics:
    """Live, negotiated details of the CURRENTLY connected network —
    genuinely different information from WifiNetwork.security, not a
    duplicate. Found live (2026-08-15): a real access point in WPA2/
    WPA3 "transition mode" (supporting both at once, common for
    backward compatibility) shows up as `security="psk"` on WifiNetwork
    (iwd's own Network.Type — and networkmanager.py's classify_security()
    deliberately makes the identical simplification for the same
    transition-mode case) while iwd's own net.connman.iwd.
    StationDiagnostic.GetDiagnostics() reports `"WPA3-Personal"` for
    that exact same live session — what actually got negotiated during
    the handshake, not the network's own generic configured type. impala
    shows this live value in its own "Device" panel; tuicc shows it as
    a separate "Connection" preview_table instead (see modules/
    connectivity.py's own _connection_diagnostics_table()) — only ever
    meaningful for whichever row is actually connected right now, so
    unlike AdapterInfo this isn't device-wide.

    Still deliberately narrower than iwd's own GetDiagnostics() reply
    (RxMode/TxMode/RxMCS/TxMCS/InactiveTime/ConnectedTime stay out) —
    those fields' exact units/formatting weren't confirmed live, and
    showing a wrong unit as fact would be worse than not showing the
    field at all (same "degrade honestly" instinct as everywhere else
    here). frequency/channel/rssi/cipher/tx_bitrate/rx_bitrate are all
    standard, unambiguous units once confirmed.

    tx_bitrate/rx_bitrate: confirmed live (2026-08-15) against this
    session's own machine — GetDiagnostics' TxBitrate/RxBitrate are
    u32 in nl80211's own standard "100kbit/s units" convention (e.g.
    TxBitrate=1444 → 144.4 Mb/s), not raw Mb/s or bytes — cross-checked
    against the same reply's own RxMode/RxMCS ("802.11n"/14) for a
    plausibility match, not just trusted blind. NetworkManager's own
    Device.Wireless.Bitrate is a single value (already Kb/s, no TX/RX
    split) — genuinely nothing for rx_bitrate (or rssi) there. Fixed
    2026-08-24: rather than leave both as an honest gap on the NM
    backend, networkmanager.py's own get_connection_diagnostics() now
    shells out to `iw dev <iface> link` for these two specifically (the
    kernel's own nl80211 view, confirmed live against this same
    session's real connection) — best-effort, optional, (None, None)
    on anything from a missing `iw` binary to just not being connected,
    never breaking the rest of an otherwise-D-Bus-sourced result. See
    networkmanager.py's own _read_iw_link() docstring for the full
    reasoning. cipher remains a real gap on that backend (no directly
    equivalent property, `iw`'s own output doesn't cleanly carry it
    either) — see get_connection_diagnostics()'s own docstring there.

    ip_address: NOT reachable from either backend's own D-Bus tree at
    all (see netinfo.py's own docstring) — both backends fetch it the
    identical way, via the OS itself, keyed off whichever interface
    name that backend already resolved for get_adapter_info().
    """
    frequency: int | None = None  # MHz
    channel: int | None = None
    security: str | None = None  # raw backend string ("WPA3-Personal", ...) — NOT normalized onto WifiNetwork.security's own "psk"/"sae"/... vocabulary, deliberately: this is the live daemon's own label, shown as-is
    rssi: int | None = None  # signed dBm
    cipher: str | None = None  # e.g. "CCMP-128" — NetworkManager backend leaves this None, see get_connection_diagnostics()'s own docstring there
    tx_bitrate: float | None = None  # Mb/s
    rx_bitrate: float | None = None  # Mb/s — NetworkManager backend gets this from `iw dev <iface> link`, not D-Bus (which has no TX/RX split in its own Bitrate property)
    ip_address: str | None = None  # IPv4, dotted-quad — see netinfo.get_ipv4_address()


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


@dataclass
class BluetoothAdapterInfo:
    """The controller/radio itself — mirrors WiFi's own AdapterInfo,
    scoped to exactly what has a real action behind it (2026-08-16, see
    CLAUDE/NOTES/design-decisions.md for the fuller writeup): Powered
    and Pairable both get a toggle key; Discovering already has one
    (`scan`, wired since R4) and deliberately isn't a field here — see
    below. Discoverable exists on org.bluez.Adapter1 too (confirmed
    live) but has no action behind it in this pass, so it's left out
    rather than shown as inert info.
    """
    name: str | None = None  # interface id (e.g. "hci0"), from the adapter's own D-Bus object path — NOT Adapter1's own "Name" property, which is the machine's Bluetooth broadcast name (e.g. the hostname), not an interface identifier. Mirrors WiFi's own AdapterInfo.name convention.
    address: str | None = None  # MAC
    powered: bool | None = None
    pairable: bool | None = None
    # discovering is NOT a field here, deliberately: the existing
    # bluetooth_discovering Domain (poll_interval=0.5, tighter than
    # this adapter snapshot's own default interval) is the live truth
    # for a blinking "Scanning" indicator — same reasoning WiFi's own
    # AdapterInfo already documents for its own Scanning field.
    # modules/connectivity.py's _bt_adapter_info_table() takes
    # discovering as a separate function parameter instead.
