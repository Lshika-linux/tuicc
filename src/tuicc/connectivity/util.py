"""Shared helpers for connectivity backends."""

from jeepney import DBusAddress, DBusErrorResponse, MessageType, new_method_call


def dbus_call(connection, bus_name, path, interface, member, signature="", body=(), timeout=5):
    """Make one D-Bus method call and return its reply body. Shared by
    iwd.py/bluez.py (short-lived `io.blocking.DBusConnection`) and the
    agents (persistent `io.threading.DBusRouter`) — both expose the
    same `send_and_get_reply(msg, timeout=...)` shape, so this stays
    connection-type-agnostic. `timeout` defaults to 5s; a call that can
    legitimately take much longer (e.g. iwd.py's Network.Connect()
    waiting on a full agent passphrase round-trip) must pass its own,
    longer value explicitly.

    Raises `jeepney.DBusErrorResponse` when the daemon replies with a
    real D-Bus ERROR message (e.g. `org.freedesktop.DBus.Error.
    ServiceUnknown` when the target service isn't running) instead of
    returning its body as if it were valid data — found live: this
    used to return `reply.body` unconditionally regardless of
    `reply.header.message_type`, so an ERROR reply's body (always a
    single string, e.g. "The name org.freedesktop.NetworkManager was
    not provided by any .service files") got silently handed to
    callers as real return data. In networkmanager.py's
    `_find_wifi_device()` that meant a ServiceUnknown error string got
    assigned to `device_paths`, and `for path in device_paths` iterated
    it char-by-char, feeding single characters as bogus object paths
    into the next call — the real cause (NetworkManager not running)
    was completely masked by an unrelated `ValueError` several calls
    later. Same blind-return risk existed for every other caller
    (iwd.py/bluez.py's own `_call()`, the three agents' `RegisterAgent`
    calls in `start()` — a failed registration would have looked like
    a success, `self._error` never set). `DBusErrorResponse` is
    jeepney's own exception (it already builds `.name`/`.data` off a
    raw reply message), reused here rather than inventing a duplicate
    — VISION.md's no-silent-failure discipline: a D-Bus failure must
    propagate as a real exception so callers up the stack (StatusWorker's
    poll wrapper, each agent's own `except Exception` in `start()`) can
    record the real `last_error`/`self._error`, not something else
    entirely.
    """
    addr = DBusAddress(path, bus_name=bus_name, interface=interface)
    msg = new_method_call(addr, member, signature, body)
    reply = connection.send_and_get_reply(msg, timeout=timeout)
    if reply.header.message_type == MessageType.error:
        raise DBusErrorResponse(reply)
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
