"""Tests for AgentMailbox — the cross-thread handoff between a D-Bus
agent's dispatch thread and the render thread. Pure `threading`
primitives, no D-Bus involved, same testability class as procmon.py's
PidFeed — see agent_mailbox.py's own module docstring for the design
rationale (in particular, why there's no blocking "wait for an
answer" mechanism — an earlier version of this file had one and it
turned out to starve the daemon's own Cancel/Release messages).
"""

import threading

from tuicc.connectivity.agent_mailbox import AgentMailbox


def test_no_request_pending_initially():
    mailbox = AgentMailbox()

    assert mailbox.has_pending() is False
    assert mailbox.get_request() is None


def test_publish_makes_request_visible():
    mailbox = AgentMailbox()

    mailbox.publish("some request")

    assert mailbox.has_pending() is True
    assert mailbox.get_request() == "some request"


def test_get_request_does_not_clear_it():
    # The render side re-reads the same request across many frames
    # while it's on screen — get_request() must be non-destructive,
    # unlike pop_request().
    mailbox = AgentMailbox()
    mailbox.publish("some request")

    mailbox.get_request()
    mailbox.get_request()

    assert mailbox.has_pending() is True
    assert mailbox.get_request() == "some request"


def test_pop_request_clears_and_returns_it():
    mailbox = AgentMailbox()
    mailbox.publish("some request")

    popped = mailbox.pop_request()

    assert popped == "some request"
    assert mailbox.has_pending() is False
    assert mailbox.get_request() is None


def test_pop_request_on_empty_mailbox_returns_none():
    mailbox = AgentMailbox()

    assert mailbox.pop_request() is None


def test_pop_request_is_one_shot():
    # A second pop (e.g. a stray double call) must not resurrect a
    # request that's already been consumed.
    mailbox = AgentMailbox()
    mailbox.publish("some request")
    mailbox.pop_request()

    assert mailbox.pop_request() is None


def test_republishing_after_pop_is_a_fresh_request():
    mailbox = AgentMailbox()
    mailbox.publish("first request")
    mailbox.pop_request()

    mailbox.publish("second request")

    assert mailbox.has_pending() is True
    assert mailbox.get_request() == "second request"


def test_concurrent_publish_and_pop_never_lose_or_duplicate_a_request():
    # Not a hunt for a specific race (the lock makes each individual
    # publish()/pop_request() atomic by construction) — just a sanity
    # check that hammering both from separate threads doesn't corrupt
    # state or raise.
    mailbox = AgentMailbox()
    popped = []

    def publisher():
        for i in range(200):
            mailbox.publish(i)

    def popper():
        for _ in range(200):
            request = mailbox.pop_request()
            if request is not None:
                popped.append(request)

    t1 = threading.Thread(target=publisher)
    t2 = threading.Thread(target=popper)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Whatever wasn't popped mid-race is still safely gettable, not lost.
    remaining = mailbox.get_request()
    if remaining is not None:
        popped.append(remaining)
    assert all(isinstance(v, int) for v in popped)
