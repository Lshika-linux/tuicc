"""Tests for iwd_agent.py's build_reply_for — the pure method-name ->
D-Bus-reply-shape logic, kept separate from the live dispatch loop's
socket/thread glue so it's testable with no D-Bus connection at all.
The dispatch loop itself, RegisterAgent, and the live filter/queue
plumbing are genuinely untestable without a real iwd — same category
as iwd.py's own connect()/disconnect() today.

The one piece of stop() that IS worth testing despite that: whether it
replies to a still-pending request before tearing the connection down
— see IwdAgent.stop()'s own docstring for why (found live, suspected
trigger for a real iwd crash). A stub router/thread
stands in for the live D-Bus objects; jeepney's own message
construction builds a realistic enough fake incoming call for
new_error() to consume.
"""

from jeepney import DBusAddress, HeaderFields, new_method_call

from tuicc.connectivity.iwd_agent import (
    CANCELED_ERROR,
    UNKNOWN_METHOD_ERROR,
    IwdAgent,
    PassphraseRequest,
    build_reply_for,
)


def _request():
    return PassphraseRequest(ssid="Home", network_path="/net/connman/iwd/0/4", _msg=None)


def test_request_passphrase_returns_the_typed_answer():
    kind, sig, body = build_reply_for("RequestPassphrase", _request(), "hunter2")

    assert kind == "return"
    assert sig == "s"
    assert body == ("hunter2",)


def test_request_private_key_passphrase_replies_canceled():
    kind, name, body = build_reply_for("RequestPrivateKeyPassphrase", _request(), None)

    assert kind == "error"
    assert name == CANCELED_ERROR
    assert body == ()


def test_request_username_and_password_replies_canceled():
    kind, name, body = build_reply_for("RequestUserNameAndPassword", _request(), None)

    assert kind == "error"
    assert name == CANCELED_ERROR


def test_request_user_password_replies_canceled():
    kind, name, body = build_reply_for("RequestUserPassword", _request(), None)

    assert kind == "error"
    assert name == CANCELED_ERROR


def test_unrecognized_method_replies_unknown_method():
    # Genuinely out-of-spec — must still be a real, honest D-Bus
    # error, not silence (VISION.md's R3 discipline), and distinct
    # from CANCELED_ERROR (a real, in-spec "not supported yet" case).
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
        # Stands in for UnregisterAgent's own reply — stop() doesn't
        # care about the body, just that the call doesn't raise.
        class _Reply:
            body = ((),)
        return _Reply()

    def close(self):
        pass


def _fake_incoming_call():
    # A real jeepney Message, same shape a live RequestPassphrase call
    # would have — enough for new_error()'s own header.serial/sender
    # lookups to work without a live connection.
    addr = DBusAddress("/org/tuicc/WifiAgent", bus_name="net.connman.iwd", interface="net.connman.iwd.Agent")
    return new_method_call(addr, "RequestPassphrase", "o", ("/net/connman/iwd/0/4/x",))


def test_stop_replies_to_a_still_pending_request_before_cleanup():
    agent = IwdAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router
    msg = _fake_incoming_call()
    agent.mailbox.publish(PassphraseRequest(ssid="Home", network_path="/net/connman/iwd/0/4/x", _msg=msg))

    agent.stop()  # _cleanup() nulls agent._router — capture via the `router` local instead

    assert len(router.sent) == 1
    reply = router.sent[0]
    assert reply.header.fields[HeaderFields.reply_serial] == msg.header.serial
    assert reply.header.fields[HeaderFields.error_name] == CANCELED_ERROR
    # The mailbox itself is empty afterward — nothing left to reply to twice.
    assert agent.mailbox.has_pending() is False


def test_stop_with_nothing_pending_sends_no_reply():
    agent = IwdAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router

    agent.stop()

    assert router.sent == []
