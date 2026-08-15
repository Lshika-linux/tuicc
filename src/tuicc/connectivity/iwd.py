"""iwd backend — talks to iwd directly over D-Bus (net.connman.iwd)
rather than parsing iwctl's terminal output. iwctl's get-networks
table only shows signal quality as 1-4 colored asterisks meant for
terminal display — the real signal strength (in centi-dBm) is only
available this way, via Station.GetOrderedNetworks().
"""

from jeepney.io.blocking import open_dbus_connection

from tuicc.connectivity.base import WifiBackend
from tuicc.connectivity.model import WifiNetwork, AdapterInfo
from tuicc.connectivity.util import dbus_call

BUS_NAME = "net.connman.iwd"

# Network.Connect() on a network needing credentials it doesn't have
# cached blocks until iwd's own agent round-trip resolves: iwd calls
# our registered IwdAgent's RequestPassphrase (which publishes an
# AgentMailbox request and returns immediately — see agent_mailbox.py's
# own module docstring for why nothing blocks waiting for an answer),
# and iwd's Connect() call itself only gets its reply once the user
# actually types something and confirms, all the way over in main.py's
# mode_stack "connectivity_passphrase" tier. That can legitimately
# take much longer than a plain property read. Generous but bounded:
# if the user just walks away without answering, iwd's own agent-
# request timeout (not under this codebase's control) eventually
# cancels the request from iwd's side, sending our agent Cancel —
# IwdAgent's dispatch loop notices that immediately (it never blocks
# waiting on us), so this Connect() call itself fails with a real iwd
# error long before this deadline matters.
CONNECT_TIMEOUT_SECONDS = 120


def _call(connection, path, interface, member, signature="", body=(), timeout=5):
    return dbus_call(connection, BUS_NAME, path, interface, member, signature, body, timeout)


def find_station_path_in_objects(objects):
    """Pure logic half of _find_station_path: given the dict returned
    by ObjectManager.GetManagedObjects (path -> {interface: props}),
    find the first one implementing net.connman.iwd.Station. Testable
    without any D-Bus connection.
    """
    for path, interfaces in objects.items():
        if "net.connman.iwd.Station" in interfaces:
            return path
    return None


def _find_station_path(connection):
    """The object path of the first device in station mode, found by
    walking net.connman.iwd's ObjectManager tree rather than assuming
    a fixed path (adapter/device numbering isn't guaranteed stable).
    """
    objects = _call(
        connection, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
    )[0]
    return find_station_path_in_objects(objects)


def find_adapter_path_in_objects(objects):
    """Same shape as find_station_path_in_objects above, for the
    PARENT net.connman.iwd.Adapter object (the physical radio, e.g.
    "phy0") rather than the Device/Station object underneath it —
    needed by get_adapter_info() for Model/Vendor/SupportedModes,
    which live on the Adapter, not the Device.
    """
    for path, interfaces in objects.items():
        if "net.connman.iwd.Adapter" in interfaces:
            return path
    return None


def _find_adapter_path(connection):
    objects = _call(
        connection, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"
    )[0]
    return find_adapter_path_in_objects(objects)


def _prop(props, name):
    """A GetAll reply's props dict has each value as a (type_code,
    value) tuple (jeepney's variant convention) — this pulls just the
    value, None if the property is absent entirely rather than KeyError,
    same "degrade honestly" tolerance _security_label()'s unknown-value
    fallback already uses elsewhere in this module.
    """
    entry = props.get(name)
    return entry[1] if entry is not None else None


def _build_adapter_info(adapter_props, device_props, station_props):
    """Pure logic half of get_adapter_info() — testable without a real
    D-Bus connection. Combines all three objects' own property sets
    into one flat AdapterInfo, same "modules/connectivity.py doesn't
    need to know these are 3 separate D-Bus objects" reasoning
    AdapterInfo's own docstring gives. Device's own Powered (if
    present) wins over Adapter's — NOT YET LIVE-CONFIRMED which of the
    two actually carries it on a real machine (this whole Adapter-vs-
    Device property split needs a `busctl introspect` check against a
    real iwd, see get_adapter_info()'s own docstring below); checking
    Device first costs nothing if it turns out to only ever live on
    Adapter.
    """
    return AdapterInfo(
        name=_prop(device_props, "Name"),
        address=_prop(device_props, "Address"),
        model=_prop(adapter_props, "Model"),
        vendor=_prop(adapter_props, "Vendor"),
        supported_modes=_prop(adapter_props, "SupportedModes"),
        mode=_prop(device_props, "Mode"),
        powered=_prop(device_props, "Powered") if _prop(device_props, "Powered") is not None else _prop(adapter_props, "Powered"),
        state=_prop(station_props, "State"),
    )


def _signal_to_percent(centi_dbm):
    """Rough dBm -> percent mapping: -30 dBm (excellent, very close to
    the AP) is 100%, -90 dBm (unusable) is 0%, linear in between.
    """
    dbm = centi_dbm / 100
    percent = round((dbm + 90) / (90 - 30) * 100)
    return max(0, min(100, percent))


def _connect_succeeded(station_props, network_path):
    """Pure logic half of connect()'s own post-attempt check: given
    Station's Properties.GetAll reply (read immediately after
    Connect() returns) and the network path just attempted, whether the
    connection actually took. See
    CLAUDE/NOTES/design-decisions.md#iwd-connect-false-success for why
    this check exists at all. Testable without any D-Bus connection.
    """
    connected_network = station_props.get("ConnectedNetwork")
    return connected_network is not None and connected_network[1] == network_path


def _network_name_connected_known(connection, network_path):
    """'known' means iwd has stored credentials for this network —
    Connect() works without a password prompt for these; an unknown
    network connects via an interactive passphrase prompt instead (see
    agent_mailbox.py/iwd_agent.py), so `known` is threaded through
    rather than used to filter networks out. Deliberately lighter than
    _build_wifi_network: connect() below only needs a name to match, so
    it skips the extra KnownNetwork round trip.
    """
    props = _call(
        connection, network_path, "org.freedesktop.DBus.Properties", "GetAll",
        "s", ("net.connman.iwd.Network",),
    )[0]
    name = props["Name"][1]
    connected = props["Connected"][1]
    known = "KnownNetwork" in props
    return name, connected, known


def _build_wifi_network(connection, network_path, signal):
    """Everything get_networks() surfaces per network, for the row
    itself AND modules/connectivity.py's hover-preview info. `Type`
    (security)/`Name`/`Connected`/`KnownNetwork` all come from the ONE
    Properties.GetAll call below (no extra round trip beyond what this
    backend already made before the preview feature existed) — only
    for networks that ARE known does this make one further GetAll, to
    the KnownNetwork object itself, for AutoConnect/Hidden/
    LastConnectedTime (meaningless for a network with no stored
    profile at all).
    """
    props = _call(
        connection, network_path, "org.freedesktop.DBus.Properties", "GetAll",
        "s", ("net.connman.iwd.Network",),
    )[0]
    known_network = props.get("KnownNetwork")

    auto_connect = hidden = last_connected = None
    if known_network is not None:
        kn_props = _call(
            connection, known_network[1], "org.freedesktop.DBus.Properties", "GetAll",
            "s", ("net.connman.iwd.KnownNetwork",),
        )[0]
        auto_connect = kn_props["AutoConnect"][1]
        hidden = kn_props["Hidden"][1]
        last_connected_prop = kn_props.get("LastConnectedTime")
        last_connected = last_connected_prop[1] if last_connected_prop is not None else None

    return WifiNetwork(
        ssid=props["Name"][1],
        connected=props["Connected"][1],
        signal=signal,
        known=known_network is not None,
        security=props["Type"][1],
        auto_connect=auto_connect,
        hidden=hidden,
        last_connected=last_connected,
    )


class IwdBackend(WifiBackend):
    def get_networks(self) -> list[WifiNetwork]:
        """No internal try/except Exception here — a D-Bus failure (iwd
        not running, SYSTEM bus unreachable, ...) propagates naturally,
        so status_worker.StatusWorker's poll wrapper can catch it and
        record last_error instead of it vanishing into a bare `[]`
        indistinguishable from "no networks around" (see CLAUDE/VISION.md's
        R3, no-silent-failure). Only resource cleanup (closing the
        connection) needs its own try/finally — it must run whether
        get_networks succeeds, raises, or returns early.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return []

            ordered = _call(
                connection, station_path, "net.connman.iwd.Station", "GetOrderedNetworks"
            )[0]

            networks = []
            for network_path, centi_dbm in ordered:
                networks.append(_build_wifi_network(connection, network_path, _signal_to_percent(centi_dbm)))
            return networks
        finally:
            connection.close()

    def connect(self, ssid: str) -> None:
        """Raises on failure — StatusWorker's action-dispatch wrapper
        catches it and records it via get_action_error_for(), which is
        what modules/connectivity.py's hover-preview and the
        passphrase-entry overlay both surface to the user.

        Does its own post-attempt verification rather than trusting a
        clean Connect() return — see
        CLAUDE/NOTES/design-decisions.md#iwd-connect-false-success for
        why: iwd's Connect() can return successfully even when the
        actual authentication attempt failed.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return
            ordered = _call(
                connection, station_path, "net.connman.iwd.Station", "GetOrderedNetworks"
            )[0]
            for network_path, _signal in ordered:
                name, _connected, _known = _network_name_connected_known(connection, network_path)
                if name == ssid:
                    # See CONNECT_TIMEOUT_SECONDS' own comment — an
                    # unknown network's Connect() blocks on the full
                    # IwdAgent round-trip, not just a plain D-Bus call.
                    _call(
                        connection, network_path, "net.connman.iwd.Network", "Connect",
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    )
                    # See this method's own docstring — Connect()
                    # returning cleanly is NOT proof the connection
                    # actually succeeded.
                    station_props = _call(
                        connection, station_path, "org.freedesktop.DBus.Properties", "GetAll",
                        "s", ("net.connman.iwd.Station",),
                    )[0]
                    if not _connect_succeeded(station_props, network_path):
                        raise RuntimeError(f"Failed to connect to {ssid} — check the passphrase and try again")
                    break
        finally:
            connection.close()

    def disconnect(self) -> None:
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path:
                _call(connection, station_path, "net.connman.iwd.Station", "Disconnect")
        finally:
            connection.close()

    def scan(self) -> None:
        """Fire-and-forget: Station.Scan() schedules iwd's own scan and
        returns immediately — it does NOT wait for the scan to finish
        or return fresh results. See is_scanning() below for how
        modules/connectivity.py actually knows when the real scan
        (whatever its real duration turns out to be) is still running;
        this call itself being "pending" on StatusWorker only lasts as
        long as this one D-Bus round trip, nowhere near the real scan
        window.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path:
                _call(connection, station_path, "net.connman.iwd.Station", "Scan")
        finally:
            connection.close()

    def is_scanning(self) -> bool:
        """Station.Scanning — the real ground truth for whether iwd's
        scan (triggered by scan() or anything else, e.g. iwctl) is
        still in progress. Polled as its own StatusWorker Domain rather
        than folded into get_networks()'s poll: is_pending("wifi", ...)
        alone clears almost immediately since scan() itself returns
        fast, which would make the "Scanning…" label flicker.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return False
            props = _call(
                connection, station_path, "org.freedesktop.DBus.Properties", "GetAll",
                "s", ("net.connman.iwd.Station",),
            )[0]
            scanning_prop = props.get("Scanning")
            return scanning_prop[1] if scanning_prop is not None else False
        finally:
            connection.close()

    def forget(self, ssid: str) -> None:
        """KnownNetwork.Forget() — deletes iwd's own stored credentials
        for this network. Same "walk GetOrderedNetworks, match by name"
        pattern connect() already uses to find the right network path;
        a network with no KnownNetwork object at all (never was known
        to begin with) is silently a no-op, matching connect()'s own
        "not found, nothing happens" tolerance rather than raising.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return
            ordered = _call(
                connection, station_path, "net.connman.iwd.Station", "GetOrderedNetworks"
            )[0]
            for network_path, _signal in ordered:
                props = _call(
                    connection, network_path, "org.freedesktop.DBus.Properties", "GetAll",
                    "s", ("net.connman.iwd.Network",),
                )[0]
                if props["Name"][1] != ssid:
                    continue
                known_network = props.get("KnownNetwork")
                if known_network is not None:
                    _call(connection, known_network[1], "net.connman.iwd.KnownNetwork", "Forget")
                break
        finally:
            connection.close()

    def connect_hidden(self, ssid: str) -> None:
        """Station.ConnectHiddenNetwork() — same agent-passphrase round
        trip as connect() if the network needs credentials (hence the
        same generous CONNECT_TIMEOUT_SECONDS), just addressed by a
        typed name instead of a network object path since a hidden
        network never shows up in GetOrderedNetworks() to begin with.

        KNOWN GAP, deliberately unfixed for now (backlogged, no way to
        test it live currently — see CLAUDE/NOTES/known-limitations.md
        #connect-hidden-silent-failure): unlike connect() above, this
        does NOT verify ConnectedNetwork afterward, so if
        ConnectHiddenNetwork() shares connect()'s own false-success
        quirk on a wrong passphrase (unconfirmed but plausible — same
        Station auth machinery), a wrong passphrase here would silently
        report success instead of raising.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return
            _call(
                connection, station_path, "net.connman.iwd.Station", "ConnectHiddenNetwork",
                "s", (ssid,), timeout=CONNECT_TIMEOUT_SECONDS,
            )
        finally:
            connection.close()

    def get_adapter_info(self) -> AdapterInfo | None:
        """None only when no adapter/station could be found at all —
        see this method's own "genuinely nothing there" contract on
        WifiBackend. The exact Adapter-vs-Device property split below
        (_build_adapter_info's own docstring) is modeled on a real
        impala screenshot, not yet independently confirmed against
        `busctl introspect net.connman.iwd <path>` on a real machine —
        do that once while this is still being tested live, per this
        repo's own "empirical debugging" discipline.
        """
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            station_path = _find_station_path(connection)
            adapter_path = _find_adapter_path(connection)
            if station_path is None and adapter_path is None:
                return None
            device_props = {}
            station_props = {}
            if station_path is not None:
                device_props = _call(
                    connection, station_path, "org.freedesktop.DBus.Properties", "GetAll",
                    "s", ("net.connman.iwd.Device",),
                )[0]
                station_props = _call(
                    connection, station_path, "org.freedesktop.DBus.Properties", "GetAll",
                    "s", ("net.connman.iwd.Station",),
                )[0]
            adapter_props = {}
            if adapter_path is not None:
                adapter_props = _call(
                    connection, adapter_path, "org.freedesktop.DBus.Properties", "GetAll",
                    "s", ("net.connman.iwd.Adapter",),
                )[0]
            return _build_adapter_info(adapter_props, device_props, station_props)
        finally:
            connection.close()

    def set_powered(self, powered: bool) -> None:
        """Properties.Set on the Adapter's own Powered property — the
        whole radio, not just this one station/device (iwd models
        power at the Adapter level; see get_adapter_info()'s own
        Device-vs-Adapter Powered fallback for the same ambiguity,
        still needing live confirmation)."""
        connection = open_dbus_connection(bus="SYSTEM")
        try:
            adapter_path = _find_adapter_path(connection)
            if adapter_path is None:
                return
            _call(
                connection, adapter_path, "org.freedesktop.DBus.Properties", "Set",
                "ssv", ("net.connman.iwd.Adapter", "Powered", ("b", powered)),
            )
        finally:
            connection.close()
