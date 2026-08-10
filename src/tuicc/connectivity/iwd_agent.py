"""iwd_agent.py — the D-Bus "Agent" tuicc registers with iwd so that
Network.Connect() on an unknown/credential-needing network can prompt
the user interactively (RequestPassphrase) instead of failing. See
agent_mailbox.py's own module docstring for the cross-thread handoff
design and why the dispatch loop never blocks waiting for an answer.
iwd.py's CONNECT_TIMEOUT_SECONDS is the other half of the same
round-trip this file drives.

Why this can't be a StatusWorker Domain, and why it needs
jeepney.io.threading (not io.blocking, which the rest of connectivity/
uses): see VISION.md's R4 section — a Domain.poll() call can't listen
indefinitely without stalling every other domain on StatusWorker's one
shared thread, and RegisterAgent needs a connection that stays open
for as long as the daemon should be able to call back into it, which
io.blocking's per-call open/close pattern doesn't provide.
"""

import queue
import threading
from dataclasses import dataclass

from jeepney import HeaderFields, MatchRule, MessageFlag, MessageType, new_error, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection

from tuicc.connectivity.agent_mailbox import AgentMailbox
from tuicc.connectivity.util import dbus_call

BUS_NAME = "net.connman.iwd"
AGENT_MANAGER_PATH = "/net/connman/iwd"
AGENT_MANAGER_INTERFACE = "net.connman.iwd.AgentManager"
AGENT_PATH = "/org/tuicc/WifiAgent"

CANCELED_ERROR = "net.connman.iwd.Agent.Error.Canceled"
UNKNOWN_METHOD_ERROR = "org.freedesktop.DBus.Error.UnknownMethod"

# How long the dispatch loop's queue.get() blocks per iteration before
# re-checking the stop flag — short enough that stop() returns
# promptly, long enough not to busy-loop.
POLL_INTERVAL_SECONDS = 0.5


@dataclass
class PassphraseRequest:
    ssid: str
    network_path: str
    _msg: object  # the raw jeepney Message this request answers — internal, agent-only


def build_reply_for(member, request, answer):
    """Pure logic: given the incoming method's name and (for
    RequestPassphrase specifically) the user's typed answer, decide
    what to send back. Returns ("return", signature, body) for a
    normal reply, or ("error", error_name, body) for a D-Bus error
    reply. Kept separate from the live dispatch loop's socket/thread
    glue so it's fixture-testable with no D-Bus connection — same
    split iwd.py's own find_station_path_in_objects gets.
    """
    if member == "RequestPassphrase":
        return "return", "s", (answer,)
    if member in ("RequestPrivateKeyPassphrase", "RequestUserNameAndPassword", "RequestUserPassword"):
        # Rare EAP/enterprise cases, out of MVP scope (VISION.md's R4
        # section) — a real, honest D-Bus error, not silence.
        return "error", CANCELED_ERROR, ()
    return "error", UNKNOWN_METHOD_ERROR, ()


class IwdAgent:
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
        """No-op if already running. Registration failure (iwd not
        running, SYSTEM bus unreachable) is caught and stored via
        get_error() rather than raised — mirrors CavaReader.start()'s
        "missing binary isn't a crash" carve-out — but unlike cava
        this IS meant to be visibly surfaced: main.py folds
        get_error() into the existing wifi_error field, since an
        unregistered agent silently means unknown-network connects
        just keep failing with no indication why.
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
                "RegisterAgent", "o", (AGENT_PATH,),
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
        with a Canceled error BEFORE tearing the connection down —
        found live, genuinely reproducible: main.py's finally block
        (or a plain Ctrl+C) calling this while a passphrase overlay is
        still open used to just close the connection out from under
        iwd, leaving its own blocked Network.Connect() call waiting on
        a peer that had simply vanished mid-request instead of getting
        a normal, in-protocol answer. Suspected (not proven — no way
        to reproduce a daemon segfault on demand) trigger for a real
        iwd crash (SIGSEGV, systemd auto-restarted it) hit live this
        session while doing exactly this kind of abrupt-disconnect
        testing. Even setting the crash aside, there's no reason to
        rely on iwd handling a dropped peer gracefully when a clean
        Canceled reply costs nothing. Best-effort UnregisterAgent
        afterward — we're tearing the connection down either way, so a
        failure there (iwd already gone, bus already closing) isn't
        worth surfacing.
        """
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self.cancel_current()
        try:
            dbus_call(
                self._router, BUS_NAME, AGENT_MANAGER_PATH, AGENT_MANAGER_INTERFACE,
                "UnregisterAgent", "o", (AGENT_PATH,),
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
        passphrase. Builds and sends the real D-Bus reply directly —
        safe from this thread (DBusRouter.send() is lock-protected,
        see agent_mailbox.py's module docstring for why nothing needs
        to block waiting for this call).
        """
        request = self.mailbox.pop_request()
        if request is None:
            return
        kind, sig, body = build_reply_for("RequestPassphrase", request, passphrase)
        self._send_reply(request._msg, kind, sig, body)

    def cancel_current(self) -> None:
        """User pressed Escape — replies with a real Canceled error.
        Distinct from the daemon-initiated Cancel/Release path in
        _handle_message below, which sends no reply at all (the
        daemon already gave up waiting for one by the time it sends
        that).
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

        if member in ("Cancel", "Release"):
            # The daemon itself is giving up on whatever's currently
            # pending (network went away, iwd's own timeout fired,
            # ...) — clear it without replying to the original
            # RequestPassphrase; nothing is waiting for that reply
            # anymore.
            self.mailbox.pop_request()
            return

        if member == "RequestPassphrase":
            network_path = msg.body[0]
            ssid = self._lookup_network_name(network_path)
            self.mailbox.publish(PassphraseRequest(ssid=ssid, network_path=network_path, _msg=msg))
            return

        # Rare/unimplemented method — reply with a real error rather
        # than silence, per VISION.md's R3 discipline.
        kind, name, body = build_reply_for(member, None, None)
        if not no_reply:
            self._send_reply(msg, kind, name, body)

    def _lookup_network_name(self, network_path):
        props = dbus_call(
            self._router, BUS_NAME, network_path, "org.freedesktop.DBus.Properties", "GetAll",
            "s", ("net.connman.iwd.Network",),
        )[0]
        return props["Name"][1]
