"""AgentMailbox — the single-slot, thread-safe handoff between a
D-Bus agent's dispatch thread (iwd_agent.py/bluez_agent.py, which
receives RequestPassphrase/RequestConfirmation/... calls from iwd/
bluez) and the render thread (which owns the actual UI prompt and,
eventually, the user's answer).

Same "expose the live object, not a snapshot" convention as
RenderContext.cava (CavaReader) and procmon.py's PidFeed. One instance
per agent — iwd's pending passphrase request and bluez's pending
pairing request are unrelated and must never share a mailbox.

Deliberately does NOT block anyone waiting for an answer (an earlier
design here had the agent's own dispatch thread call a blocking
wait_for_answer() after publish() — found, during iwd_agent.py's
implementation, to be a real bug: that thread is the SAME one that
must later notice the daemon sending Cancel/Release on this exact
request, e.g. because the network went out of range or the daemon's
own internal timeout fired. If the dispatch thread is blocked inside
wait_for_answer(), it can never get back to receiving the Cancel/
Release message that's supposed to end that same wait — a real
starvation bug, not just an inefficiency). Instead, `pop_request()` is
called directly by whichever side ends the request's life — the agent
replying with the user's real answer (reply_passphrase()/
reply_pairing()), the agent replying with a Canceled/Rejected error
because the user pressed Escape (cancel_current()), or the dispatch
loop itself when the daemon sends Cancel/Release (no reply needed at
all there, see iwd_agent.py/bluez_agent.py's dispatch loops) — each
call site decides what, if anything, to send back; the mailbox itself
only tracks "is something pending right now."
"""

import threading


class AgentMailbox:
    def __init__(self):
        self._lock = threading.Lock()
        self._request = None

    def publish(self, request) -> None:
        """Called from the agent's dispatch thread when a new callback
        (RequestPassphrase/RequestConfirmation/...) arrives."""
        with self._lock:
            self._request = request

    def get_request(self):
        """Called from the render thread: the currently pending
        request, or None if nothing's pending. Read-only — does not
        clear it (unlike pop_request()), so the render side can keep
        re-reading the same request across frames while it's shown."""
        with self._lock:
            return self._request

    def has_pending(self) -> bool:
        with self._lock:
            return self._request is not None

    def pop_request(self):
        """Atomically clear and return the current request (or None if
        nothing's pending). The one mutator every request-ending code
        path goes through — see this module's own docstring for why
        there's no separate blocking "wait for an answer" mechanism.
        """
        with self._lock:
            request, self._request = self._request, None
            return request
