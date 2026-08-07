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
where that distinction actually reaches the screen.

Action failures (e.g. a connect() call raising) get the same
treatment, in a separate `_action_errors` dict (get_action_error()) —
this WAS left as a known, deliberately deferred gap when R3 first
landed ("revisit if that turns out to matter in practice"), and it did:
found live building R5's control module — a toggle's command failing
fast (gammastep exiting immediately for lack of a GeoClue2 provider,
on this session's own test machine) produced zero visible feedback,
the toggle just silently stayed in its old state. _action_errors is
its own dict, not reused from poll's `_errors` above, because _run()
always re-polls every domain in the same loop iteration right after
processing that iteration's actions (see the `actions or now -
last_poll > ...` check below) — writing an action error into the same
slot poll() writes to would get silently clobbered by that very poll's
own (likely successful) result before any caller read it. Cleared at
the start of every new action attempt for the same domain, not by a
subsequent poll.
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
    # None (the default) means "use StatusWorker's own shared
    # poll_interval" — most domains (wifi/bluetooth scans,
    # control.toggle status checks) are genuinely fine refreshing every
    # few seconds. A domain can override with its own, shorter value
    # when that's not true — found live: audio (default sink can change
    # out from under tuicc — WirePlumber auto-switches to a newly
    # connected bluetooth device on its own, tuicc didn't initiate it —
    # see main.py's own audio/media Domain construction) and media
    # (a song changing mid-playback) both felt "stuck" at the 5s shared
    # default, reported directly by the user testing it live.
    poll_interval: float | None = None


class StatusWorker:
    def __init__(self, domains: list[Domain], poll_interval=5):
        self._domains = {domain.name: domain for domain in domains}
        self._poll_interval = poll_interval  # shared default — see Domain.poll_interval's own docstring
        # Per-domain, not one shared value — a domain with its own
        # (shorter) poll_interval becomes due on its own schedule,
        # independent of every other domain's.
        self._last_poll = {name: 0.0 for name in self._domains}

        self._lock = threading.Lock()
        # None (not []) until the first poll actually completes, same
        # "unknown, not yet empty" value a real error also produces —
        # see get()'s docstring for why that's the right initial value,
        # not a silent-seeming [].
        self._snapshots = {name: None for name in self._domains}
        self._errors = {name: None for name in self._domains}
        # A domain's last REQUESTED ACTION's error, kept separate from
        # _errors (poll errors) above — found live matters in practice
        # (see this class's own module docstring): _run() always
        # re-polls every domain in the same loop iteration right after
        # processing actions, so writing an action failure into the
        # same _errors slot would get silently overwritten by that
        # very poll's own (likely successful) result before a caller
        # ever got a chance to read it.
        self._action_errors = {name: None for name in self._domains}

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

    def get_action_error(self, domain_name):
        """The exception message from domain_name's last REQUESTED
        ACTION, or None if it last succeeded (or no action has been
        requested yet). Cleared at the start of every new action
        attempt for that domain (see _run()) — not by a subsequent
        successful poll, unlike get_error() above; a poll and an
        action are different things that just happen to share a
        _run() iteration.
        """
        with self._lock:
            return self._action_errors[domain_name]

    def is_pending(self, domain_name, key):
        with self._lock:
            return (domain_name, key) in self._pending

    def has_pending(self):
        with self._lock:
            return len(self._pending) > 0

    def request_action(self, domain_name, action_name, arg=None, pending_key=None):
        """arg is whatever the registered action callable needs — for
        wifi/bluetooth that's always been a single id (ssid/device_id),
        but a domain whose action needs more than one piece of data
        (audio's set_volume(sink_id, percent), found live building
        R5's audio/ backend) can pass a tuple/whatever shape its own
        Domain.actions callable expects to unpack.

        pending_key is what is_pending()/has_pending() actually track
        — defaults to arg itself (unchanged behavior for every
        existing caller, wifi/bluetooth's own id-is-both-things case).
        Pass it explicitly when arg isn't a sensible pending identity
        on its own — audio's set_volume wants "is THIS SINK being
        adjusted" (sink_id alone), not "is this exact (sink_id,
        percent) tuple pending" (which would never match a second,
        different volume request for the same sink still in flight).
        """
        if pending_key is None:
            pending_key = arg
        with self._lock:
            self._action_queue.append((domain_name, action_name, arg, pending_key))
            self._pending.add((domain_name, pending_key))

    def _run(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue

            with self._lock:
                actions = self._action_queue[:]
                self._action_queue.clear()

            acted_domain_names = {domain_name for domain_name, _, _, _ in actions}

            for domain_name, action_name, arg, pending_key in actions:
                domain = self._domains.get(domain_name)
                with self._lock:
                    self._action_errors[domain_name] = None
                try:
                    if domain is not None:
                        action = domain.actions.get(action_name)
                        if action is not None:
                            action(arg)
                except Exception as e:
                    with self._lock:
                        self._action_errors[domain_name] = str(e) or type(e).__name__
                finally:
                    with self._lock:
                        self._pending.discard((domain_name, pending_key))

            # Per-domain due check, not one shared "poll everything or
            # nothing" gate — a domain just acted on is always re-polled
            # immediately (so an action's real effect gets confirmed
            # promptly, same reasoning as before), everything else
            # becomes due on its OWN poll_interval (Domain.poll_interval
            # if set, else this worker's shared default).
            now = time.monotonic()
            due = {
                name: domain for name, domain in self._domains.items()
                if name in acted_domain_names
                or now - self._last_poll[name] > (
                    domain.poll_interval if domain.poll_interval is not None else self._poll_interval
                )
            }
            if due:
                results = {}
                errors = {}
                for name, domain in due.items():
                    try:
                        results[name] = domain.poll()
                        errors[name] = None
                    except Exception as e:
                        results[name] = None
                        errors[name] = str(e) or type(e).__name__
                    self._last_poll[name] = now
                with self._lock:
                    self._snapshots.update(results)
                    self._errors.update(errors)

            time.sleep(0.2)
