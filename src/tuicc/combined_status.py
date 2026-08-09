"""CombinedStatus: the single object ctx.status/ActionContext.status
actually is — routes each domain name to whichever underlying worker
(status_worker.StatusWorker, the pull/poll worker, or
push_worker.PushWorker, the event-driven one) actually owns it, so no
module ever needs to know or care which mechanism a given domain uses.
See push_worker.py's own module docstring for why tuicc has two worker
classes instead of one — this facade is the seam that makes that an
internal wiring decision (main.py's), invisible everywhere else.

Every method here just looks up the owning worker and forwards — this
class owns no state of its own (no lock, no snapshots), matching how
thin main.py's own dispatch code stays elsewhere in this codebase
(render.py's draw_all/collect_nav_items, same "look it up, delegate,
nothing clever" shape).
"""


class CombinedStatus:
    def __init__(self, pull_worker, push_worker):
        pull_names = pull_worker.domain_names()
        push_names = push_worker.domain_names()
        overlap = pull_names & push_names
        if overlap:
            # A domain registered with BOTH workers is a wiring bug in
            # main.py, not a runtime condition to degrade gracefully
            # from — silently letting one shadow the other would mean
            # main.py's own domain list changed the app's behavior
            # depending on dict iteration order, exactly the kind of
            # silent-wrong-answer this codebase's no-silent-failure
            # principle rules out.
            raise ValueError(f"domain(s) registered with both pull and push workers: {sorted(overlap)}")

        self._pull = pull_worker
        self._push = push_worker
        self._owner = {name: pull_worker for name in pull_names}
        self._owner.update({name: push_worker for name in push_names})

    def _worker_for(self, domain_name):
        # An unregistered domain name is a caller bug (every real call
        # site passes a name main.py actually registered) — falling
        # back to the pull worker here just reproduces whatever error
        # StatusWorker's own direct-indexing get()/get_error() already
        # raises for an unknown name, rather than masking it with a
        # DIFFERENT failure mode depending on which worker happens to
        # be "first".
        return self._owner.get(domain_name, self._pull)

    def start(self):
        self._pull.start()
        self._push.start()

    def stop(self):
        self._pull.stop()
        self._push.stop()

    def pause(self):
        self._pull.pause()
        self._push.pause()

    def resume(self):
        self._pull.resume()
        self._push.resume()

    def get(self, domain_name):
        return self._worker_for(domain_name).get(domain_name)

    def get_error(self, domain_name):
        return self._worker_for(domain_name).get_error(domain_name)

    def get_action_error(self, domain_name):
        return self._worker_for(domain_name).get_action_error(domain_name)

    def is_pending(self, domain_name, key):
        return self._worker_for(domain_name).is_pending(domain_name, key)

    def has_pending(self) -> bool:
        return self._pull.has_pending() or self._push.has_pending()

    def request_action(self, domain_name, action_name, arg=None, pending_key=None):
        self._worker_for(domain_name).request_action(domain_name, action_name, arg, pending_key)
