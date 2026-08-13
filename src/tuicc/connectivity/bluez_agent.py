"""bluez_agent.py — the D-Bus "Agent" tuicc registers with bluez so
that pairing a new device (driven by bluez.py's connect(), which calls
Device1.Pair() first for an unpaired device) can prompt the user
interactively instead of failing. Structurally mirrors iwd_agent.py —
see that module's docstring and agent_mailbox.py's own module
docstring for the shared cross-thread handoff design and why the
dispatch loop never blocks waiting for an answer. bluez.py's
PAIR_TIMEOUT_SECONDS is the other half of the same round-trip this
file drives.

Registered capability is "KeyboardDisplay" (bluez's own default for
an empty string) — the maximally-compatible choice for a curses TUI,
which can both display text and accept typed input, so every method
bluez might call stays reachable regardless of which one the pairing
negotiation on the remote device's side picks.
"""

import queue
import threading
from dataclasses import dataclass

from jeepney import HeaderFields, MatchRule, MessageFlag, MessageType, new_error, new_method_return
from jeepney.io.threading import DBusRouter, open_dbus_connection

from tuicc.connectivity.agent_mailbox import AgentMailbox
from tuicc.connectivity.util import dbus_call

BUS_NAME = "org.bluez"
AGENT_MANAGER_PATH = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_PATH = "/org/tuicc/BluetoothAgent"
AGENT_CAPABILITY = "KeyboardDisplay"

REJECTED_ERROR = "org.bluez.Error.Rejected"
UNKNOWN_METHOD_ERROR = "org.freedesktop.DBus.Error.UnknownMethod"

POLL_INTERVAL_SECONDS = 0.5

# kind values: "confirm" (RequestConfirmation, has a passkey to show
# for the user to compare against the device's own display) or
# "authorize" ("Just Works" pairing, RequestAuthorization, nothing to
# compare — plain accept/reject).
CONFIRM = "confirm"
AUTHORIZE = "authorize"


@dataclass
class PairingRequest:
    kind: str
    device_path: str
    device_name: str
    device_id: str  # the device's Address (MAC) — same identity bluez.py's own BluetoothDevice.id and
    # request_action()'s own pending_key use, needed so main.py's "waiting for the real pair/connect
    # result" state can check StatusWorker.is_pending()/get_action_error_for() against the right key
    passkey: int | None  # only set for kind == CONFIRM
    _msg: object  # the raw jeepney Message this request answers — internal, agent-only


def build_reply_for(member, request, answer):
    """Pure logic, same split as iwd_agent.py's build_reply_for — kept
    separate from the live dispatch loop so it's fixture-testable with
    no D-Bus connection.
    """
    if member in ("RequestConfirmation", "RequestAuthorization"):
        if answer:
            return "return", None, ()
        return "error", REJECTED_ERROR, ()
    if member in ("RequestPasskey", "DisplayPasskey", "RequestPinCode", "DisplayPinCode", "AuthorizeService"):
        # Legacy/rare device-capability combinations, out of MVP scope
        # (VISION.md's R4 section) — a real, honest D-Bus error, not
        # silence.
        return "error", REJECTED_ERROR, ()
    return "error", UNKNOWN_METHOD_ERROR, ()


class BluezAgent:
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
        """No-op if already running. See IwdAgent.start()'s docstring
        for the registration-failure-handling rationale this mirrors.
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
                "RegisterAgent", "os", (AGENT_PATH, AGENT_CAPABILITY),
            )
            dbus_call(
                self._router, BUS_NAME, AGENT_MANAGER_PATH, AGENT_MANAGER_INTERFACE,
                "RequestDefaultAgent", "o", (AGENT_PATH,),
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
        (Rejected, via cancel_current()) before tearing the connection
        down, then best-effort UnregisterAgent — same
        abrupt-disconnect-while-pending risk as IwdAgent.stop(), see
        CLAUDE/NOTES/known-limitations.md#agent-shutdown-cancels-pending
        (only iwd was confirmed to actually crash from it, but the same
        risk applies here for bluez).
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

    def reply_pairing(self, accept: bool) -> None:
        """Called from the render thread once the UI has a yes/no
        answer. Works for both RequestConfirmation and
        RequestAuthorization — same reply shape (empty return on
        accept, Rejected error on reject) for either kind.
        """
        request = self.mailbox.pop_request()
        if request is None:
            return
        member = "RequestConfirmation" if request.kind == CONFIRM else "RequestAuthorization"
        kind, name_or_sig, body = build_reply_for(member, request, accept)
        self._send_reply(request._msg, kind, name_or_sig, body)

    def cancel_current(self) -> None:
        """User pressed Escape — same as an explicit reject. Distinct
        from the daemon-initiated Cancel/Release path in
        _handle_message below, which sends no reply at all."""
        self.reply_pairing(False)

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
        # Unlike iwd's Cancel(reason)/Release(), bluez's Cancel()/
        # Release() aren't necessarily [noreply] in every bluez
        # version — checked generically via the message's own header
        # flag rather than assumed per interface, same as iwd_agent.py.
        no_reply = bool(msg.header.flags & MessageFlag.no_reply_expected)

        if member in ("Cancel", "Release"):
            self.mailbox.pop_request()
            if not no_reply:
                self._router.send(new_method_return(msg))
            return

        if member in ("RequestConfirmation", "RequestAuthorization"):
            device_path = msg.body[0]
            passkey = msg.body[1] if member == "RequestConfirmation" else None
            device_name, device_id = self._lookup_device_info(device_path)
            kind = CONFIRM if member == "RequestConfirmation" else AUTHORIZE
            self.mailbox.publish(PairingRequest(
                kind=kind, device_path=device_path, device_name=device_name,
                device_id=device_id, passkey=passkey, _msg=msg,
            ))
            return

        # Rare/unimplemented method — reply with a real error rather
        # than silence, per VISION.md's R3 discipline.
        kind, name, body = build_reply_for(member, None, None)
        if not no_reply:
            self._send_reply(msg, kind, name, body)

    def _lookup_device_info(self, device_path):
        props = dbus_call(
            self._router, BUS_NAME, device_path, "org.freedesktop.DBus.Properties", "GetAll",
            "s", ("org.bluez.Device1",),
        )[0]
        # Alias is always present (bluez defaults it to Name, or the
        # address itself) — same reasoning as bluez.py's own
        # _device_from_props. Address is always present too — it's the
        # same MAC-as-id identity bluez.py's BluetoothDevice.id uses.
        return props["Alias"][1], props["Address"][1]
