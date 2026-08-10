"""Shared helpers for connectivity backends."""

from jeepney import DBusAddress, new_method_call


def dbus_call(connection, bus_name, path, interface, member, signature="", body=(), timeout=5):
    """Make one D-Bus method call and return its reply body.

    Promoted out of iwd.py's original module-private `_call()`, which
    hardcoded `bus_name` to "net.connman.iwd" as a closure constant —
    parameterized here so bluez.py's D-Bus rewrite (and the new
    iwd/bluez agents' own outbound calls) can share one helper instead
    of three near-identical copies. `connection` can be either an
    `io.blocking.DBusConnection` (bluez.py/iwd.py's short-lived,
    one-call-then-close style) or an `io.threading.DBusRouter` (the
    agents' persistent-connection style) — both expose the same
    `send_and_get_reply(msg, timeout=...)` shape, so this helper is
    connection-type-agnostic.

    `timeout` defaults to a short 5s (fine for a plain property read
    or an instant action) but is a real parameter, not a hardcoded
    constant — a caller whose call can legitimately take much longer
    (iwd.py's Network.Connect() on an unknown network, which now waits
    on the whole agent round-trip: iwd calls our registered IwdAgent's
    RequestPassphrase, which publishes an AgentMailbox request and
    returns immediately — the user has to actually type something and
    confirm before IwdAgent.reply_passphrase() sends iwd its reply,
    which is what finally lets iwd's own Connect() call return) must
    pass its own, longer timeout explicitly.
    """
    addr = DBusAddress(path, bus_name=bus_name, interface=interface)
    msg = new_method_call(addr, member, signature, body)
    reply = connection.send_and_get_reply(msg, timeout=timeout)
    return reply.body


def decode_ssid(ssid_bytes: bytes) -> str:
    """NetworkManager's own AccessPoint.Ssid (and the ssid embedded in
    a saved connection profile's 802-11-wireless.ssid setting) is `ay`
    — a raw byte array, not a string — unlike iwd, which already hands
    back a decoded Name string. 802.11 doesn't actually guarantee an
    SSID is valid UTF-8 (real devices with non-UTF-8 SSIDs exist, if
    rare), so this is a real edge case, not just ceremony — tolerant
    decode (replacing anything invalid) rather than letting a raw,
    malformed SSID crash network enumeration entirely.

    Shared between networkmanager.py (AP enumeration) and
    networkmanager_agent.py (decoding the ssid straight out of
    GetSecrets' own connection dict, no extra D-Bus round trip needed
    for that) — deliberately a narrow exception to iwd.py/iwd_agent.py's
    own precedent of NOT sharing constants between a backend and its
    agent (BUS_NAME is duplicated in both today): this is real, tested
    logic with an edge case, not a bare constant.
    """
    return bytes(ssid_bytes).decode("utf-8", errors="replace")
