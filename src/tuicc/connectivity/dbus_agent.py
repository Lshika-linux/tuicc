"""Connection/dispatch plumbing shared by every D-Bus agent.

BluezAgent, IwdAgent and NetworkManagerAgent all open a system-bus
connection, filter method calls at one object path, pump them on a
daemon thread and answer with a method-return or an error. Only that
mechanical part lives here; registration (which manager, which
methods) and the answers themselves stay in each agent, since those
are what actually differ per daemon.
"""

import queue
import threading

from jeepney import new_error, new_method_return

from tuicc.connectivity.agent_mailbox import AgentMailbox

# How long the dispatch loop's queue.get() blocks per iteration before
# re-checking the stop flag — short enough that stop() returns
# promptly, long enough not to busy-loop.
POLL_INTERVAL_SECONDS = 0.5


class DBusAgentBase:
    """Not an ABC on purpose: BluezAgent has no interface of its own,
    and the two wifi agents already get theirs from WifiAgent. Listed
    first in their bases so its concrete get_error() satisfies
    WifiAgent's abstract one."""

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
