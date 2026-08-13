"""Generic background-polling worker: thread + action queue + pending
set + cached-snapshot-per-domain + poll interval — the pattern
connectivity/worker.py's own ConnectivityWorker used to hardcode for
wifi+bluetooth specifically, generalized so control/media/system
monitor domains can register against the same worker instead of each
writing their own thread/lock/queue plumbing. See CLAUDE/VISION.md's R3
section for why there's no thin ConnectivityWorker wrapper class kept
around this, and for the poll_interval-per-domain story.

No-silent-failure lands here: a domain's poll() exception is captured
into that domain's own last_error, and the cached snapshot becomes None
(not []) for that round, so a module can tell "genuinely nothing there"
apart from "couldn't check" — see modules/connectivity.py's _build_rows.

Action failures (e.g. a connect() call raising) get the same treatment
in a separate `_action_errors` dict, not reused from poll's `_errors` —
_run() always re-polls every domain right after processing that
iteration's actions, so sharing a slot would let a successful poll
silently clobber an unread action error. Cleared at the start of every
new action attempt for the domain, not by a subsequent poll.
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
    # poll_interval". A domain can override with a shorter value when
    # something outside tuicc's own control can change it between
    # polls (audio's default sink, media's now-playing) — see
    # CLAUDE/VISION.md's R3 section for the concrete story.
    poll_interval: float | None = None


class StatusWorker:
    def __init__(self, domains: list[Domain], poll_interval=5):
        self._domains = {domain.name: domain for domain in domains}
        self._poll_interval = poll_interval  # shared default — see Domain.poll_interval's own docstring
        # Per-domain, not one shared value — a domain with its own
        # (shorter) poll_interval becomes due on its own schedule,
        # independent of every other domain's. -inf, not 0.0: the due
        # check is `now - last_poll > interval` against
        # time.monotonic(), whose reference point is undefined (on a
        # freshly-started container it can start near 0) — 0.0 would
        # make the first poll wait out poll_interval on such a
        # machine instead of firing immediately.
        self._last_poll = {name: float("-inf") for name in self._domains}

        self._lock = threading.Lock()
        # None (not []) until the first poll actually completes, same
        # "unknown, not yet empty" value a real error also produces —
        # see get()'s docstring for why that's the right initial value,
        # not a silent-seeming [].
        self._snapshots = {name: None for name in self._domains}
        self._errors = {name: None for name in self._domains}
        # A domain's last requested action's error, kept separate from
        # _errors (poll errors) above — see the module docstring for why.
        self._action_errors = {name: None for name in self._domains}
        # Which request_action() pending_key the current _action_errors
        # entry actually belongs to — see get_action_error_for()'s own
        # docstring for why a plain per-DOMAIN error isn't enough for a
        # domain whose items share one domain name (wifi/bluetooth:
        # many networks/devices, one "wifi"/"bluetooth" domain each).
        self._action_error_keys = {name: None for name in self._domains}

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

    def domain_names(self) -> set:
        """The set of domain names this worker owns — combined_status.py's
        CombinedStatus facade uses this (and push_worker.py's own
        identical method) once at construction time to build its
        domain-name -> worker routing table, so modules never need to
        know which of the two workers a given domain actually lives in.
        """
        return set(self._domains.keys())

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

    def get_action_error_for(self, domain_name, key):
        """Same as get_action_error(domain_name) above, but returns None
        unless the error actually belongs to THIS specific key.
        get_action_error() alone is domain-wide, exactly right for
        control.py (one "toggle:i" domain per item) but wrong for wifi/
        bluetooth, where many networks/devices share one domain —
        without this, selecting a different network after an earlier
        one's connect attempt failed would still show that earlier
        network's stale error. `key` is the same identity
        request_action()'s `pending_key` param uses.
        """
        with self._lock:
            if self._action_error_keys[domain_name] != key:
                return None
            return self._action_errors[domain_name]

    def is_pending(self, domain_name, key):
        with self._lock:
            return (domain_name, key) in self._pending

    def has_pending(self):
        with self._lock:
            return len(self._pending) > 0

    def request_action(self, domain_name, action_name, arg=None, pending_key=None):
        """arg is whatever the registered action callable needs — a
        single id (ssid/device_id) for wifi/bluetooth, or a
        tuple/whatever shape a domain's own action expects (e.g.
        audio's set_volume(sink_id, percent)). pending_key is what
        is_pending()/has_pending() track — defaults to arg, but should
        be passed explicitly when arg isn't itself a sensible pending
        identity (audio wants "is THIS SINK adjusted", not "is this
        exact tuple pending").
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
                    self._action_error_keys[domain_name] = pending_key
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
