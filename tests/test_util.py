"""Tests for connectivity/util.py's decode_ssid() — the one piece of
this file genuinely new for the NetworkManager backend (dbus_call()
itself is already exercised indirectly through iwd.py's/bluez.py's own
tests, no dedicated tests of its own needed here).
"""

from tuicc.connectivity.util import decode_ssid


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
