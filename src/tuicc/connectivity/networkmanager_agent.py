"""networkmanager_agent.py — the D-Bus "secret agent" tuicc registers
with NetworkManager so that activating an unknown/credential-needing
network can prompt the user interactively (GetSecrets) instead of
failing. Mirrors iwd_agent.py's shape closely — same AgentMailbox
cross-thread handoff, same io.threading persistent connection + one
dispatch thread, same stop()-replies-before-disconnect discipline —
see iwd_agent.py's own module docstring for the reasoning behind both
of those, unchanged here.

Differs from IwdAgent only where NetworkManager's own protocol
genuinely differs — see each site below (AGENT_PATH being fixed
rather than self-chosen, GetSecrets carrying the connection dict
inline so no follow-up lookup call is needed, the nested-variant reply
shape).

Honesty note, same as networkmanager.py's own: NetworkManager isn't
installed on the machine this was built on, so build_reply_for() is
fixture-tested the same way iwd_agent.py's own is, but the live
dispatch loop and RegisterWithCapabilities/GetSecrets round trip
against a real NetworkManager have NOT been confirmed.
"""

import queue
import threading
from dataclasses import dataclass

from jeepney import HeaderFields, MatchRule, MessageFlag, MessageType, new_error, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection

from tuicc.connectivity.agent_mailbox import AgentMailbox
from tuicc.connectivity.base import WifiAgent
from tuicc.connectivity.util import dbus_call, decode_ssid

BUS_NAME = "org.freedesktop.NetworkManager"
AGENT_MANAGER_PATH = "/org/freedesktop/NetworkManager/AgentManager"
AGENT_MANAGER_INTERFACE = "org.freedesktop.NetworkManager.AgentManager"
# Fixed/conventional path, NOT self-chosen like iwd's AGENT_PATH —
# confirmed against NetworkManager's own first-party test suite
# (src/tests/test-secret-agent.py). REGISTER_IDENTIFIER is a separate
# reverse-DNS string, not a path.
AGENT_PATH = "/org/freedesktop/NetworkManager/SecretAgent"
REGISTER_IDENTIFIER = "org.tuicc.WifiAgent"

CANCELED_ERROR = "org.freedesktop.NetworkManager.SecretAgent.Error.UserCanceled"
UNKNOWN_METHOD_ERROR = "org.freedesktop.DBus.Error.UnknownMethod"

SETTING_NAME = "802-11-wireless-security"
SECRET_KEY = "psk"

# How long the dispatch loop's queue.get() blocks per iteration before
# re-checking the stop flag — same reasoning as iwd_agent.py's own.
POLL_INTERVAL_SECONDS = 0.5


@dataclass
class PassphraseRequest:
    ssid: str
    network_path: str  # the connection's Settings.Connection path, NOT a device/AP path
    _msg: object  # the raw jeepney Message this request answers — internal, agent-only


def build_reply_for(member, request, answer):
    """Pure logic: mirrors iwd_agent.py's own build_reply_for exactly
    in shape, different only in the reply's D-Bus signature —
    NetworkManager expects a nested a{sa{sv}} secrets dict, not a bare
    string. Kept separate from the live dispatch loop so it's
    fixture-testable with no D-Bus connection.
    """
    if member == "GetSecrets":
        body = ({SETTING_NAME: {SECRET_KEY: ("s", answer)}},)
        return "return", "a{sa{sv}}", body
    return "error", UNKNOWN_METHOD_ERROR, ()


class NetworkManagerAgent(WifiAgent):
    def __init__(self):
        self.mailbox = AgentMailbox()
        self._connection = None
        self._router = None
        self._filter = None
        self._thread = None
        self._stop = threading.Event()
        self._error = None  # str | None — a real registration/setup failure only

    def get_error(self):
        return self._error

    def start(self) -> None:
        """No-op if already running. Registration failure (NetworkManager
        not running, SYSTEM bus unreachable) is caught and stored via
        get_error() rather than raised — same carve-out as IwdAgent.start().
        """
        if self._thread is not None:
            return
        self._error = None
        try:
            self._connection = open_dbus_connection(bus="SYSTEM")
            self._router = DBusRouter(self._connection)
            rule = MatchRule(type="method_call", path=AGENT_PATH)
            self._filter = self._router.filter(rule)
            dbus_call(
                self._router, BUS_NAME, AGENT_MANAGER_PATH, AGENT_MANAGER_INTERFACE,
                "RegisterWithCapabilities", "su", (REGISTER_IDENTIFIER, 0),
            )
        except Exception as e:
            self._error = str(e) or type(e).__name__
            self._cleanup()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """No-op if not running. Replies to any still-pending request
        with a UserCanceled error BEFORE tearing the connection down —
        same reasoning as IwdAgent.stop()'s own docstring: no reason to
        rely on NetworkManager handling a dropped peer mid-GetSecrets
        any more gracefully than iwd turned out to. Best-effort
        Unregister afterward.
        """
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self.cancel_current()
        try:
            dbus_call(
                self._router, BUS_NAME, AGENT_MANAGER_PATH, AGENT_MANAGER_INTERFACE,
                "Unregister", "", (),
            )
        except Exception:
            pass
        self._cleanup()

    def _cleanup(self):
        if self._filter is not None:
            self._filter.close()
        if self._router is not None:
            self._router.close()
        if self._connection is not None:
            self._connection.close()
        self._filter = None
        self._router = None
        self._connection = None
        self._thread = None

    def reply_passphrase(self, passphrase: str) -> None:
        """Called from the render thread once the UI has a typed
        passphrase. Same shape as IwdAgent.reply_passphrase() — safe
        from this thread, DBusRouter.send() is lock-protected.
        """
        request = self.mailbox.pop_request()
        if request is None:
            return
        kind, sig, body = build_reply_for("GetSecrets", request, passphrase)
        self._send_reply(request._msg, kind, sig, body)

    def cancel_current(self) -> None:
        """User pressed Escape — replies with a real UserCanceled
        error. Distinct from the daemon-initiated CancelGetSecrets path
        in _handle_message below, which sends no reply at all.
        """
        request = self.mailbox.pop_request()
        if request is None:
            return
        self._router.send(new_error(request._msg, CANCELED_ERROR))

    def _send_reply(self, msg, kind, name_or_sig, body):
        if kind == "return":
            self._router.send(new_method_return(msg, name_or_sig, body))
        else:
            self._router.send(new_error(msg, name_or_sig, None, body))

    def _dispatch_loop(self):
        while not self._stop.is_set():
            try:
                msg = self._filter.queue.get(timeout=POLL_INTERVAL_SECONDS)
            except queue.Empty:
                continue
            self._handle_message(msg)

    def _handle_message(self, msg):
        if msg.header.message_type != MessageType.method_call:
            return
        member = msg.header.fields.get(HeaderFields.member)
        no_reply = bool(msg.header.flags & MessageFlag.no_reply_expected)

        if member == "CancelGetSecrets":
            # NetworkManager itself is giving up on whatever's
            # currently pending — clear it without replying to the
            # original GetSecrets; nothing is waiting for that reply
            # anymore. Same as iwd's Cancel/Release handling.
            self.mailbox.pop_request()
            return

        if member == "GetSecrets":
            connection_settings, connection_path, setting_name, _hints, _flags = msg.body
            ssid = self._ssid_from_connection(connection_settings)
            self.mailbox.publish(PassphraseRequest(ssid=ssid, network_path=connection_path, _msg=msg))
            return

        if member in ("SaveSecrets", "DeleteSecrets"):
            # Out of MVP scope, same honest-error treatment as iwd's
            # rare EAP methods — a real D-Bus error, not silence.
            if not no_reply:
                self._send_reply(msg, "error", CANCELED_ERROR, ())
            return

        # Rare/unimplemented method — reply with a real error rather
        # than silence, per VISION.md's R3 discipline.
        kind, name, body = build_reply_for(member, None, None)
        if not no_reply:
            self._send_reply(msg, kind, name, body)

    def _ssid_from_connection(self, connection_settings):
        """GetSecrets' own call body already carries the full
        connection settings dict — unlike IwdAgent._lookup_network_name(),
        no follow-up D-Bus round trip is needed to find the ssid.
        """
        wireless = connection_settings.get("802-11-wireless", {})
        ssid_prop = wireless.get("ssid")
        if ssid_prop is None:
            return ""
        return decode_ssid(ssid_prop[1])
