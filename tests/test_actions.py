"""Tests for actions.py — BASE_HANDLERS, with a mock provider so no real
WM connection is needed.
"""

from types import SimpleNamespace

from tuicc.actions import BASE_HANDLERS, ActionContext


class _FakeProvider:
    def __init__(self):
        self.focused_region = None
        self.focused_window = None

    def focus_region(self, region_id):
        self.focused_region = region_id

    def focus_window(self, window_id):
        self.focused_window = window_id


def test_region_handler_calls_focus_region():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, connectivity=None)
    item = SimpleNamespace(focus_target="workspace-3")

    should_exit, pending = BASE_HANDLERS["region"](ctx, item, cfg=None)

    assert provider.focused_region == "workspace-3"
    assert should_exit is True
    assert pending is None


def test_window_handler_calls_focus_window():
    provider = _FakeProvider()
    ctx = ActionContext(provider=provider, connectivity=None)
    item = SimpleNamespace(focus_target="window-42")

    should_exit, pending = BASE_HANDLERS["window"](ctx, item, cfg=None)

    assert provider.focused_window == "window-42"
    assert should_exit is True
    assert pending is None


def test_base_handlers_has_exactly_region_and_window():
    assert set(BASE_HANDLERS.keys()) == {"region", "window"}
