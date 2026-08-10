"""iwd backend — talks to iwd directly over D-Bus (net.connman.iwd)
rather than parsing iwctl's terminal output. iwctl's get-networks
table only shows signal quality as 1-4 colored asterisks meant for
terminal display — the real signal strength (in centi-dBm) is only
available this way, via Station.GetOrderedNetworks().
"""

from jeepney.io.blocking import open_dbus_connection

from tuicc.connectivity.base import WifiBackend
from tuicc.connectivity.model import WifiNetwork
from tuicc.connectivity.util import dbus_call

BUS_NAME = "net.connman.iwd"

# Network.Connect() on a network needing credentials it doesn't have
# cached blocks until iwd's own agent round-trip resolves: iwd calls
# our registered IwdAgent's RequestPassphrase (which publishes an
# AgentMailbox request and returns immediately — see agent_mailbox.py's
# own module docstring for why nothing blocks waiting for an answer),
# and iwd's Connect() call itself only gets its reply once the user
# actually types something and confirms, all the way over in main.py's
# "connectivity_passphrase" input_claim tier. That can legitimately
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
    Connect() returns) and the network path just attempted, whether
    the connection actually took. See IwdBackend.connect()'s own
    docstring for why this check exists at all — found live, a wrong
    passphrase makes Connect() itself return cleanly with no error,
    ConnectedNetwork simply never becomes the attempted network.
    Testable without any D-Bus connection.
    """
    connected_network = station_props.get("ConnectedNetwork")
    return connected_network is not None and connected_network[1] == network_path


def _network_name_connected_known(connection, network_path):
    """'known' means iwd has stored credentials for this network (you
    connected to it before, from iwd or anything else feeding it the
    same known-networks store) — Connect() works without a password
    prompt for these. An unknown network now also connects fine (once
    an IwdAgent is registered — see agent_mailbox.py/iwd_agent.py) via
    an interactive passphrase prompt, so `known` is threaded through
    into WifiNetwork instead of being used to filter the network out.

    Deliberately the lighter of two similar-looking helpers — connect()
    below only needs a name to match against, called once per network
    in a scan of the whole list, so it skips _build_wifi_network's own
    extra KnownNetwork round trip entirely rather than paying that cost
    for every network just to find the one being connected to.
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
        """No internal try/except Exception here (there used to be one,
        found live to be exactly the kind of silent failure VISION.md's
        R3 exists to fix) — a D-Bus failure (iwd not running, SYSTEM
        bus unreachable, ...) propagates naturally, so
        status_worker.StatusWorker's poll wrapper can actually catch it
        and record last_error instead of it vanishing into a bare `[]`
        indistinguishable from "no networks around". Only resource
        cleanup (closing the connection) needs its own try/finally —
        it must run whether get_networks succeeds, raises, or returns
        early.
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

        Critical, found LIVE (not documented anywhere, contradicts the
        naive reading of the D-Bus Agent pattern): iwd's own
        Network.Connect() can return SUCCESSFULLY — no D-Bus error, no
        exception here — even when the actual authentication attempt
        failed. Reproduced deterministically: a deliberately wrong
        passphrase against a real AP completed Connect() in ~3.4s with
        a clean, non-error reply, while the Station's own
        ConnectedNetwork property never changed to the attempted
        network — iwd apparently considers Connect() "done" once the
        attempt has been fully processed (right passphrase or wrong),
        not "authentication actually succeeded". Trusting a clean
        Connect() return alone made a wrong passphrase look
        indistinguishable from success — no exception, nothing for
        StatusWorker to capture, nothing for the UI to show. Fixed by
        checking the Station's OWN post-attempt ConnectedNetwork
        against the network path we just tried, immediately after
        Connect() returns (no extra wait needed — by the time our
        blocking Connect() call gets its reply, iwd's own internal
        state for the same operation has already settled), and raising
        a real, honest error ourselves when they don't match.
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
        own scan (triggered by scan() above, or by anything else, e.g.
        iwctl run outside tuicc) is still in progress. Polled as its
        own StatusWorker Domain (main.py's "wifi_scanning") rather than
        folded into get_networks()'s own poll, since scan() itself is
        fire-and-forget with no duration this backend controls —
        StatusWorker.is_pending("wifi", ...) alone only reflects "we
        just sent the Scan() call a moment ago", which clears almost
        immediately since that call itself returns fast. Found live,
        reported directly: without this, the "Scanning…" label in the
        box only ever flickered instead of staying up for the real
        scan window.
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
