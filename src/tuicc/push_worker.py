"""PushWorker: event-driven counterpart to StatusWorker (status_worker.py)
— same external contract (get/get_error/get_action_error/is_pending/
has_pending/request_action/start/stop/pause/resume), completely
different internal mechanism.

Why this exists as a SEPARATE class rather than a mode/flag on
StatusWorker: StatusWorker's own _run() loop is built entirely around
"iterate every domain, check which is due, sleep 0.2s, repeat" — that
works well for polling (cheap to check N domains every tick), but a
push domain needs to BLOCK waiting for an external event (a kernel
poll() call, a subprocess's streamed output, ...) for however long that
takes, which doesn't fit a shared fixed-tick loop without multiplexing
every domain's own wait mechanism together — real complexity for no
benefit when a domain can just own its own thread instead — two independent,
narrow abstractions instead of one generic one trying to do both badly.

Each PushDomain gets its OWN dedicated thread, blocked in domain.watch()
(a generator: yields once per detected change, returns when told to
stop) — not sharing StatusWorker's tick loop at all. combined_status.py
is what makes this invisible to every module: modules never touch a
PushWorker or StatusWorker directly, only the CombinedStatus facade
that routes each domain name to whichever one actually owns it.
"""

import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PushDomain:
    """One thing this worker watches for changes. watch(stop_event) is a
    generator — blocks internally however it needs to (a kernel poll()
    call, a streaming subprocess, ...), yields once per detected change,
    and returns once stop_event is set (checked on its own schedule, not
    interrupted mid-block). refresh() is the read side — like
    Domain.poll() in status_worker.py, a zero-argument callable
    returning the domain's current state — called once immediately at
    start() (so the first paint has real data, not a wait for the first
    event) and again after every change watch() yields.
    """
    name: str
    watch: Callable[[threading.Event], object]
    refresh: Callable[[], object]
    actions: dict = field(default_factory=dict)


class PushWorker:
    def __init__(self, domains: list[PushDomain]):
        self._domains = {domain.name: domain for domain in domains}
        self._lock = threading.Lock()
        self._snapshots = {name: None for name in self._domains}
        self._errors = {name: None for name in self._domains}
        self._action_errors = {name: None for name in self._domains}
        self._pending = set()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._threads = []

    def domain_names(self) -> set:
        return set(self._domains.keys())

    def start(self):
        for domain in self._domains.values():
            self._refresh_once(domain)
            thread = threading.Thread(target=self._watch_loop, args=(domain,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self):
        self._stop.set()

    def pause(self):
        """Same architecture-now-implementation-later hook StatusWorker's
        own pause()/resume() are (see that class's own docstring) —
        skips acting on a detected change while set, without ending the
        watcher thread or losing its place in domain.watch()'s own loop.
        """
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def _refresh_once(self, domain: PushDomain):
        try:
            value = domain.refresh()
            with self._lock:
                self._snapshots[domain.name] = value
                self._errors[domain.name] = None
        except Exception as e:
            with self._lock:
                self._snapshots[domain.name] = None
                self._errors[domain.name] = str(e) or type(e).__name__

    def _watch_loop(self, domain: PushDomain):
        try:
            for _ in domain.watch(self._stop):
                if self._stop.is_set():
                    break
                if self._paused.is_set():
                    continue  # consume the event, skip the refresh
                self._refresh_once(domain)
        except Exception as e:
            # domain.watch() itself raising (not domain.refresh(), see
            # _refresh_once's own try/except) — a real failure in the
            # watch mechanism (e.g. the underlying file/subprocess
            # vanished) surfaces the same no-silent-failure way a poll
            # failure does, rather than the thread just quietly dying.
            with self._lock:
                self._errors[domain.name] = str(e) or type(e).__name__

    def get(self, domain_name):
        with self._lock:
            return self._snapshots.get(domain_name)

    def get_error(self, domain_name):
        with self._lock:
            return self._errors.get(domain_name)

    def get_action_error(self, domain_name):
        with self._lock:
            return self._action_errors.get(domain_name)

    def is_pending(self, domain_name, key):
        with self._lock:
            return (domain_name, key) in self._pending

    def has_pending(self):
        with self._lock:
            return len(self._pending) > 0

    def request_action(self, domain_name, action_name, arg=None, pending_key=None):
        """Same shape as StatusWorker's own request_action (see its
        docstring for pending_key's own reasoning) — but run on a
        short-lived thread per call instead of a shared serialized
        queue, since a push domain has no shared _run() loop to queue
        against. No ordering guarantee across two concurrent actions on
        the SAME domain — acceptable today (every current push domain
        is read-only or low-frequency); revisit with a real per-domain
        queue if a push domain ever needs one.

        Re-refreshes immediately after the action runs (mirrors
        StatusWorker's own "a domain just acted on is always re-polled
        immediately") — domain.watch() SHOULD also notice the resulting
        change on its own, but isn't guaranteed to be instant (depends
        on the underlying event source's own timing), so this covers
        the gap rather than waiting on it.
        """
        if pending_key is None:
            pending_key = arg
        domain = self._domains.get(domain_name)
        with self._lock:
            self._action_errors[domain_name] = None
            self._pending.add((domain_name, pending_key))

        def run():
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
                if domain is not None:
                    self._refresh_once(domain)

        threading.Thread(target=run, daemon=True).start()
