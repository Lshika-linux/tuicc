"""Tests for networkmanager_agent.py's build_reply_for — the pure
method-name -> D-Bus-reply-shape logic — plus the same stop()
replies-before-cleanup behavior test_iwd_agent.py already covers for
IwdAgent, reusing its exact stub-router/real-jeepney-message pattern.
The live dispatch loop and RegisterWithCapabilities/GetSecrets round
trip against a real NetworkManager are genuinely untestable without
one, same category as iwd_agent.py's own untested pieces.
"""

from jeepney import DBusAddress, HeaderFields, new_method_call

from tuicc.connectivity.networkmanager_agent import (
    CANCELED_ERROR,
    SECRET_KEY,
    SETTING_NAME,
    UNKNOWN_METHOD_ERROR,
    NetworkManagerAgent,
    PassphraseRequest,
    build_reply_for,
)


def _request():
    return PassphraseRequest(ssid="Home", network_path="/org/freedesktop/NetworkManager/Settings/1", _msg=None)


def test_get_secrets_returns_nested_variant_with_typed_answer():
    kind, sig, body = build_reply_for("GetSecrets", _request(), "hunter2")

    assert kind == "return"
    assert sig == "a{sa{sv}}"
    assert body == ({SETTING_NAME: {SECRET_KEY: ("s", "hunter2")}},)


def test_unrecognized_method_replies_unknown_method():
    # Genuinely out-of-spec — must still be a real, honest D-Bus
    # error, not silence (VISION.md's R3 discipline).
    kind, name, body = build_reply_for("SomeFutureMethod", _request(), None)

    assert kind == "error"
    assert name == UNKNOWN_METHOD_ERROR


# ---------- stop(): replies before disconnecting ----------

class _StubThread:
    def join(self, timeout=None):
        pass


class _StubRouter:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def send_and_get_reply(self, message, *, timeout=None):
        # Stands in for Unregister's own reply — stop() doesn't care
        # about the body, just that the call doesn't raise.
        class _Reply:
            body = ((),)
        return _Reply()

    def close(self):
        pass


def _fake_incoming_call():
    # A real jeepney Message, same shape a live GetSecrets call would
    # have — enough for new_error()'s own header.serial/sender lookups
    # to work without a live connection.
    addr = DBusAddress(
        "/org/freedesktop/NetworkManager/SecretAgent",
        bus_name="org.freedesktop.NetworkManager",
        interface="org.freedesktop.NetworkManager.SecretAgent",
    )
    connection_settings = {"802-11-wireless": {"ssid": ("ay", b"Home")}}
    return new_method_call(
        addr, "GetSecrets", "a{sa{sv}}osasu",
        (connection_settings, "/org/freedesktop/NetworkManager/Settings/1", SETTING_NAME, [], 0),
    )


def test_stop_replies_to_a_still_pending_request_before_cleanup():
    agent = NetworkManagerAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router
    msg = _fake_incoming_call()
    agent.mailbox.publish(
        PassphraseRequest(ssid="Home", network_path="/org/freedesktop/NetworkManager/Settings/1", _msg=msg)
    )

    agent.stop()  # _cleanup() nulls agent._router — capture via the `router` local instead

    assert len(router.sent) == 1
    reply = router.sent[0]
    assert reply.header.fields[HeaderFields.reply_serial] == msg.header.serial
    assert reply.header.fields[HeaderFields.error_name] == CANCELED_ERROR
    # The mailbox itself is empty afterward — nothing left to reply to twice.
    assert agent.mailbox.has_pending() is False


def test_stop_with_nothing_pending_sends_no_reply():
    agent = NetworkManagerAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router

    agent.stop()

    assert router.sent == []


# ---------- _ssid_from_connection ----------

def test_ssid_from_connection_decodes_bytes():
    agent = NetworkManagerAgent()
    connection_settings = {"802-11-wireless": {"ssid": ("ay", b"Cafe")}}
    assert agent._ssid_from_connection(connection_settings) == "Cafe"


def test_ssid_from_connection_missing_wireless_setting_returns_empty_string():
    agent = NetworkManagerAgent()
    assert agent._ssid_from_connection({}) == ""
