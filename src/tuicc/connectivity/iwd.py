"""iwd backend — talks to iwd directly over D-Bus (net.connman.iwd)
rather than parsing iwctl's terminal output. iwctl's get-networks
table only shows signal quality as 1-4 colored asterisks meant for
terminal display — the real signal strength (in centi-dBm) is only
available this way, via Station.GetOrderedNetworks().
"""

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from tuicc.connectivity.base import WifiBackend
from tuicc.connectivity.model import WifiNetwork

BUS_NAME = "net.connman.iwd"


def _call(connection, path, interface, member, signature="", body=()):
    addr = DBusAddress(path, bus_name=BUS_NAME, interface=interface)
    msg = new_method_call(addr, member, signature, body)
    reply = connection.send_and_get_reply(msg, timeout=5)
    return reply.body


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


def _network_name_connected_known(connection, network_path):
    """'known' means iwd has stored credentials for this network (you
    connected to it before, from iwd or anything else feeding it the
    same known-networks store) — Connect() only works without a
    password prompt for these. Networks you've never connected to
    need an interactive passphrase flow this backend doesn't
    implement yet, so they're filtered out entirely rather than shown
    as connectable when they aren't.
    """
    props = _call(
        connection, network_path, "org.freedesktop.DBus.Properties", "GetAll",
        "s", ("net.connman.iwd.Network",),
    )[0]
    name = props["Name"][1]
    connected = props["Connected"][1]
    known = "KnownNetwork" in props
    return name, connected, known


class IwdBackend(WifiBackend):
    def get_networks(self) -> list[WifiNetwork]:
        try:
            connection = open_dbus_connection(bus="SYSTEM")
        except Exception:
            return []

        try:
            station_path = _find_station_path(connection)
            if station_path is None:
                return []

            ordered = _call(
                connection, station_path, "net.connman.iwd.Station", "GetOrderedNetworks"
            )[0]

            networks = []
            for network_path, centi_dbm in ordered:
                name, connected, known = _network_name_connected_known(connection, network_path)
                if not known:
                    continue
                networks.append(WifiNetwork(
                    ssid=name,
                    connected=connected,
                    signal=_signal_to_percent(centi_dbm),
                ))
            return networks
        except Exception:
            return []
        finally:
            connection.close()

    def connect(self, ssid: str) -> None:
        try:
            connection = open_dbus_connection(bus="SYSTEM")
        except Exception:
            return

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
                    _call(connection, network_path, "net.connman.iwd.Network", "Connect")
                    break
        except Exception:
            pass
        finally:
            connection.close()

    def disconnect(self) -> None:
        try:
            connection = open_dbus_connection(bus="SYSTEM")
        except Exception:
            return

        try:
            station_path = _find_station_path(connection)
            if station_path:
                _call(connection, station_path, "net.connman.iwd.Station", "Disconnect")
        except Exception:
            pass
        finally:
            connection.close()
