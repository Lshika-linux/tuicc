"""Tests for bluez_agent.py's build_reply_for — same fixture-testable
pure-logic split as test_iwd_agent.py. The dispatch loop itself,
RegisterAgent/RequestDefaultAgent, and the live filter/queue plumbing
are genuinely untestable without a real bluez.

stop()'s reply-before-disconnect behavior is tested the same way
test_iwd_agent.py tests IwdAgent.stop() — see that file's own module
docstring for why.
"""

from jeepney import DBusAddress, HeaderFields, new_method_call

from tuicc.connectivity.bluez_agent import (
    REJECTED_ERROR,
    UNKNOWN_METHOD_ERROR,
    BluezAgent,
    PairingRequest,
    build_reply_for,
)


def _confirm_request():
    return PairingRequest(
        kind="confirm", device_path="/org/bluez/hci0/dev_AA", device_name="Speaker",
        device_id="AA:BB:CC:DD:EE:FF", passkey=123456, _msg=None,
    )


def _authorize_request():
    return PairingRequest(
        kind="authorize", device_path="/org/bluez/hci0/dev_AA", device_name="Speaker",
        device_id="AA:BB:CC:DD:EE:FF", passkey=None, _msg=None,
    )


def test_request_confirmation_accept_replies_empty_return():
    kind, sig, body = build_reply_for("RequestConfirmation", _confirm_request(), True)

    assert kind == "return"
    assert sig is None
    assert body == ()


def test_request_confirmation_reject_replies_rejected_error():
    kind, name, body = build_reply_for("RequestConfirmation", _confirm_request(), False)

    assert kind == "error"
    assert name == REJECTED_ERROR


def test_request_authorization_accept_replies_empty_return():
    kind, sig, body = build_reply_for("RequestAuthorization", _authorize_request(), True)

    assert kind == "return"
    assert body == ()


def test_request_authorization_reject_replies_rejected_error():
    kind, name, body = build_reply_for("RequestAuthorization", _authorize_request(), False)

    assert kind == "error"
    assert name == REJECTED_ERROR


def test_legacy_passkey_methods_reply_rejected():
    # Legacy/rare device-capability combinations, out of MVP scope —
    # a real, honest D-Bus error, not silence.
    for member in ("RequestPasskey", "DisplayPasskey", "RequestPinCode", "DisplayPinCode", "AuthorizeService"):
        kind, name, body = build_reply_for(member, _confirm_request(), None)
        assert kind == "error"
        assert name == REJECTED_ERROR


def test_unrecognized_method_replies_unknown_method():
    kind, name, body = build_reply_for("SomeFutureMethod", _confirm_request(), None)

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
        class _Reply:
            body = ((),)
        return _Reply()

    def close(self):
        pass


def _fake_incoming_call():
    addr = DBusAddress("/org/tuicc/BluetoothAgent", bus_name="org.bluez", interface="org.bluez.Agent1")
    return new_method_call(addr, "RequestConfirmation", "ou", ("/org/bluez/hci0/dev_AA", 123456))


def test_stop_rejects_a_still_pending_request_before_cleanup():
    agent = BluezAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router
    msg = _fake_incoming_call()
    agent.mailbox.publish(PairingRequest(
        kind="confirm", device_path="/org/bluez/hci0/dev_AA", device_name="Speaker",
        device_id="AA:BB:CC:DD:EE:FF", passkey=123456, _msg=msg,
    ))

    agent.stop()  # _cleanup() nulls agent._router — capture via the `router` local instead

    assert len(router.sent) == 1
    reply = router.sent[0]
    assert reply.header.fields[HeaderFields.reply_serial] == msg.header.serial
    assert reply.header.fields[HeaderFields.error_name] == REJECTED_ERROR
    assert agent.mailbox.has_pending() is False


def test_stop_with_nothing_pending_sends_no_reply():
    agent = BluezAgent()
    agent._thread = _StubThread()
    router = _StubRouter()
    agent._router = router

    agent.stop()

    assert router.sent == []
