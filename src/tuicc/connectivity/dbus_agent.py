"""The D-Bus plumbing every agent shares.

BluezAgent, IwdAgent and NetworkManagerAgent each open a system-bus connection, filter
method calls at one object path, pump them on a daemon thread and answer with a
method-return or an error. That part was the same code three times, byte for byte; what
differs - which manager to register with, which methods arrive, how to answer them - stays
in the subclass.
"""

import queue
import threading

from jeepney import new_error, new_method_return

from tuicc.connectivity.agent_mailbox import AgentMailbox

POLL_INTERVAL_SECONDS = 0.5


class DBusAgentBase:
    """Connection, router, filter and dispatch thread; subclasses add registration and
    message handling. Not an ABC on purpose: BluezAgent has no interface of its own, and
    the two Wi-Fi agents already inherit WifiAgent for theirs."""

    def __init__(self):
        self.mailbox = AgentMailbox()
        self._connection = None
        self._router = None
        self._filter = None
        self._thread = None
        self._stop = threading.Event()
        self._error = None

    def get_error(self):
        return self._error

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

    def _handle_message(self, msg):  # pragma: no cover - each agent answers its own calls
        raise NotImplementedError
