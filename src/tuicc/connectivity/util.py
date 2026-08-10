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
