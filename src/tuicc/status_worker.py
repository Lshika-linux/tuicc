"""Generic background-polling worker: thread + action queue + pending
set + cached-snapshot-per-domain + poll interval — the pattern
connectivity/worker.py's own ConnectivityWorker used to hardcode for
wifi+bluetooth specifically (mirrors swcc's daemon architecture, a
background worker feeding cached state to the render loop, but as a
thread within the same process — tuicc is single-process, so there's
no need for IPC, just a lock-protected shared state).

VISION.md's R3: connectivity is domain client #1 — main.py builds a
StatusWorker with "wifi"/"bluetooth" Domains directly, no
ConnectivityWorker wrapper class in between (an early version of this
refactor had one; found live that it would've just been an ~8-method
1-line-pass-through layer mirroring StatusWorker's own API with
nothing of its own, the exact redundancy R2's input_claim/old-flag
duplication turned out to be — better to touch connectivity.py's own
call sites once than carry a permanent, purposeless wrapper). R5
(control/media) and R6 (system monitor) register their own domains
against this same worker instead of each writing their own copy of
this thread/lock/queue plumbing.

No-silent-failure lands here too: a domain's poll() exception no
longer vanishes into a bare `except Exception: pass` — it's captured
into that domain's own last_error, and the cached snapshot itself
becomes None (not []) for that round, so a module rendering it can
tell "genuinely nothing there" (a real []) apart from "couldn't check"
(None + last_error) — see modules/connectivity.py's _build_rows for
where that distinction actually reaches the screen. Action failures
(e.g. a connect() call raising) are NOT yet surfaced this way — still
a bare `except Exception: pass`, same as before this refactor. Known,
documented gap, not silently "fixed" with more complexity than this
pass needs: VISION.md's own concrete no-silent-failure example
("no wifi networks... D-Bus is down") is specifically about polling,
and action pending state already gets cleared either way (see
request_action), so a failed action doesn't hang a spinner forever —
it just doesn't yet explain *why* it failed. Revisit if that turns out
to matter in practice.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Domain:
    """One thing this worker polls and/or acts on — "wifi"/"bluetooth"
    today, "control"/"media"/"system" once R5/R6 land. `poll` is a
    zero-argument callable returning the domain's current state as a
    list (WifiNetwork/BluetoothDevice objects today — whatever a
    future domain's own model type is). `actions` maps an action name
    (e.g. "connect") to a one-argument callable; a read-only domain
    (system monitor's CPU/RAM, say) just leaves it empty —
    request_action() against an unregistered action name is a no-op,
    not a crash, same "degraded, not broken" tolerance the Provider
    contract's optional methods get.
    """
    name: str
    poll: Callable[[], list]
    actions: dict = field(default_factory=dict)


class StatusWorker:
    def __init__(self, domains: list[Domain], poll_interval=5):
        self._domains = {domain.name: domain for domain in domains}
        self._poll_interval = poll_interval

        self._lock = threading.Lock()
        # None (not []) until the first poll actually completes, same
        # "unknown, not yet empty" value a real error also produces —
        # see get()'s docstring for why that's the right initial value,
        # not a silent-seeming [].
        self._snapshots = {name: None for name in self._domains}
        self._errors = {name: None for name in self._domains}

        self._action_queue = []
        self._pending = set()  # {(domain_name, key)}
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def pause(self):
        """Architecture for VISION.md's R3 hibernation hook — stops the
        poll loop from doing any work (actions or polling) while set,
        without ending the thread or losing queued/pending state.
        Implementation (actually calling this from main.py while tuicc
        is dismissed, and resume() on resummon) is deliberately NOT
        wired up yet — VISION.md calls this out explicitly as
        "architecture now, implementation later". Exists now so R5/R6's
        domains don't have to retrofit it later.
        """
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def get(self, domain_name):
        """domain_name's cached snapshot — a list, or None if the last
        poll for it errored (or hasn't completed yet, e.g. right after
        start()). Never blocks on a live poll; always reads the cache.
        """
        with self._lock:
            return self._snapshots[domain_name]

    def get_error(self, domain_name):
        """The exception message from domain_name's last poll, or None
        if it last succeeded (or hasn't been attempted yet — same
        "unknown, not a claim of success" value as get()'s own None,
        see modules/connectivity.py's _build_rows for how the two
        together decide what to render).
        """
        with self._lock:
            return self._errors[domain_name]

    def is_pending(self, domain_name, key):
        with self._lock:
            return (domain_name, key) in self._pending

    def has_pending(self):
        with self._lock:
            return len(self._pending) > 0

    def request_action(self, domain_name, action_name, arg=None):
        with self._lock:
            self._action_queue.append((domain_name, action_name, arg))
            self._pending.add((domain_name, arg))

    def _run(self):
        last_poll = 0
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue

            with self._lock:
                actions = self._action_queue[:]
                self._action_queue.clear()

            for domain_name, action_name, arg in actions:
                domain = self._domains.get(domain_name)
                try:
                    if domain is not None:
                        action = domain.actions.get(action_name)
                        if action is not None:
                            action(arg)
                except Exception:
                    # See this module's own docstring: action failures
                    # aren't surfaced via last_error yet, a known,
                    # documented gap, not this pass's scope.
                    pass
                finally:
                    with self._lock:
                        self._pending.discard((domain_name, arg))

            now = time.monotonic()
            if actions or now - last_poll > self._poll_interval:
                results = {}
                errors = {}
                for name, domain in self._domains.items():
                    try:
                        results[name] = domain.poll()
                        errors[name] = None
                    except Exception as e:
                        results[name] = None
                        errors[name] = str(e) or type(e).__name__
                with self._lock:
                    self._snapshots.update(results)
                    self._errors.update(errors)
                last_poll = now

            time.sleep(0.2)
