"""Tests for modules/media.py's ACTION_HANDLERS — handle_row,
handle_transport, and handle_output — with a fake StatusWorker, same
pattern as test_connectivity_handlers.py/test_control_handlers.py.
handle_row's own expand/collapse module state is covered in more depth
by test_media_module.py; this file only confirms it works through the
real ActionContext dispatch shape.
"""

from types import SimpleNamespace

import tuicc.modules.media as media_module
from tuicc.actions import ActionContext
from tuicc.modules.media import handle_row, handle_transport, handle_output


class _FakeStatus:
    def __init__(self):
        self.request_action_calls = []

    def request_action(self, domain_name, action_name, arg, pending_key=None):
        self.request_action_calls.append((domain_name, action_name, arg))


def test_handle_row_expands_via_real_action_context():
    media_module._expanded_bus_name = None
    ctx = ActionContext(provider=None, status=_FakeStatus())
    item = SimpleNamespace(focus_target="org.mpris.MediaPlayer2.firefox")

    should_dismiss, pending = handle_row(ctx, item, cfg=None)

    assert media_module._expanded_bus_name == "org.mpris.MediaPlayer2.firefox"
    assert ctx.reselect_item_id == "media:org.mpris.MediaPlayer2.firefox:playpause"
    assert should_dismiss is False
    assert pending is None
    media_module._expanded_bus_name = None  # leave module state clean for later tests


def test_handle_transport_prev():
    status = _FakeStatus()
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="media:0:prev", focus_target="org.mpris.MediaPlayer2.firefox")

    should_dismiss, pending = handle_transport(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending is None
    assert status.request_action_calls == [("media", "previous", "org.mpris.MediaPlayer2.firefox")]


def test_handle_transport_playpause():
    status = _FakeStatus()
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="media:0:playpause", focus_target="org.mpris.MediaPlayer2.firefox")

    handle_transport(ctx, item, cfg=None)

    assert status.request_action_calls == [("media", "play_pause", "org.mpris.MediaPlayer2.firefox")]


def test_handle_transport_next():
    status = _FakeStatus()
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="media:2:next", focus_target="org.mpris.MediaPlayer2.mpv")

    handle_transport(ctx, item, cfg=None)

    assert status.request_action_calls == [("media", "next", "org.mpris.MediaPlayer2.mpv")]


def test_handle_output_requests_set_default_sink():
    status = _FakeStatus()
    ctx = ActionContext(provider=None, status=status)
    item = SimpleNamespace(id="media:output:1", focus_target="64")

    should_dismiss, pending = handle_output(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending is None
    assert status.request_action_calls == [("audio", "set_default_sink", "64")]
