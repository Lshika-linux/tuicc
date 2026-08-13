"""Shared helpers for connectivity backends."""

from jeepney import DBusAddress, new_method_call


def dbus_call(connection, bus_name, path, interface, member, signature="", body=(), timeout=5):
    """Make one D-Bus method call and return its reply body. Shared by
    iwd.py/bluez.py (short-lived `io.blocking.DBusConnection`) and the
    agents (persistent `io.threading.DBusRouter`) — both expose the
    same `send_and_get_reply(msg, timeout=...)` shape, so this stays
    connection-type-agnostic. `timeout` defaults to 5s; a call that can
    legitimately take much longer (e.g. iwd.py's Network.Connect()
    waiting on a full agent passphrase round-trip) must pass its own,
    longer value explicitly.
    """
    addr = DBusAddress(path, bus_name=bus_name, interface=interface)
    msg = new_method_call(addr, member, signature, body)
    reply = connection.send_and_get_reply(msg, timeout=timeout)
    return reply.body


def decode_ssid(ssid_bytes: bytes) -> str:
    """NetworkManager's AccessPoint.Ssid (and a saved profile's
    802-11-wireless.ssid) is `ay`, a raw byte array — unlike iwd, which
    already hands back a decoded Name string. 802.11 doesn't guarantee
    valid UTF-8, so this tolerantly decodes (replacing anything
    invalid) rather than letting a malformed SSID crash enumeration.
    Shared between networkmanager.py and networkmanager_agent.py — a
    deliberate exception to iwd.py/iwd_agent.py's usual "don't share
    constants between backend and agent" precedent, since this is real
    tested logic with an edge case, not a bare constant.
    """
    return bytes(ssid_bytes).decode("utf-8", errors="replace")
