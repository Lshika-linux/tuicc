"""Tests for connectivity/util.py — decode_ssid(), and dbus_call()'s
own ERROR-vs-METHOD_RETURN handling (a real bug found live: dbus_call()
used to return reply.body unconditionally, so an ERROR reply's body
(a single string) was silently handed to callers as if it were valid
return data — see dbus_call()'s own docstring for the full story and
networkmanager.py's _find_wifi_device() repro).
"""

from jeepney import DBusAddress, DBusErrorResponse, HeaderFields, MessageType, new_error, new_method_call

import pytest

from tuicc.connectivity.util import dbus_call, decode_ssid


class _FakeConnection:
    """Stands in for both io.blocking.DBusConnection and
    io.threading.DBusRouter — dbus_call() only ever calls
    send_and_get_reply(), same as the real agent/backend tests' own
    fakes (see test_iwd_agent.py's _StubRouter).
    """

    def __init__(self, reply):
        self._reply = reply

    def send_and_get_reply(self, message, *, timeout=None):
        return self._reply


def test_dbus_call_returns_body_on_a_real_method_return():
    addr = DBusAddress("/org/freedesktop/NetworkManager", bus_name="org.freedesktop.NetworkManager")
    msg = new_method_call(addr, "GetAllDevices")
    from jeepney.low_level import Header, Message, MessageFlag
    header = Header(
        endianness=msg.header.endianness, message_type=MessageType.method_return,
        flags=MessageFlag.no_reply_expected, protocol_version=1, body_length=0,
        serial=2, fields={HeaderFields.reply_serial: msg.header.serial},
    )
    reply = Message(header, (["/org/freedesktop/NetworkManager/Devices/1"],))

    result = dbus_call(_FakeConnection(reply), "org.freedesktop.NetworkManager",
                        "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                        "GetAllDevices")

    assert result == (["/org/freedesktop/NetworkManager/Devices/1"],)


def test_dbus_call_raises_on_a_real_error_reply_instead_of_returning_its_body():
    # Reproduces the live repro exactly: NetworkManager not running ->
    # ServiceUnknown, body is a single string that must NOT come back
    # from dbus_call() as if it were device_paths.
    addr = DBusAddress("/org/freedesktop/NetworkManager", bus_name="org.freedesktop.NetworkManager")
    msg = new_method_call(addr, "GetAllDevices")
    error_msg = new_error(
        msg, "org.freedesktop.DBus.Error.ServiceUnknown", "s",
        ("The name org.freedesktop.NetworkManager was not provided by any .service files",),
    )

    with pytest.raises(DBusErrorResponse) as excinfo:
        dbus_call(_FakeConnection(error_msg), "org.freedesktop.NetworkManager",
                   "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                   "GetAllDevices")

    assert excinfo.value.name == "org.freedesktop.DBus.Error.ServiceUnknown"
    assert excinfo.value.data == (
        "The name org.freedesktop.NetworkManager was not provided by any .service files",
    )


def test_decode_ssid_plain_ascii():
    assert decode_ssid(b"Home") == "Home"


def test_decode_ssid_utf8_multibyte():
    # A real, plausible SSID with a non-ASCII character.
    assert decode_ssid("Kávárna".encode("utf-8")) == "Kávárna"


def test_decode_ssid_invalid_utf8_does_not_raise():
    # 802.11 doesn't guarantee a valid-UTF-8 SSID — a malformed one
    # must degrade (replacement characters), not crash enumeration.
    result = decode_ssid(b"\xff\xfe\x00bad")
    assert isinstance(result, str)


def test_decode_ssid_empty_bytes():
    assert decode_ssid(b"") == ""
