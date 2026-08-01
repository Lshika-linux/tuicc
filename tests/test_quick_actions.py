"""Tests for quick_actions.py — only the pure logic (handle). draw()
needs a real curses screen to exercise meaningfully, so it's left
untested here, same as other modules' drawing code.
"""

from types import SimpleNamespace

from tuicc.modules.quick_actions import handle


def test_handle_confirm_required_tags_pending_with_owning_module():
    # Regression: draw() only shows a pending_confirm dict if it's
    # tagged with its own module name — see the same test in
    # test_power_menu.py for why this matters.
    cfg = SimpleNamespace(quick_actions=[
        {"label": "Logout", "command": "swaymsg exit", "confirm": True, "shell_true": False},
    ])
    item = SimpleNamespace(id="quick_actions:0")

    should_exit, pending = handle(ctx=None, item=item, cfg=cfg)

    assert should_exit is False
    assert pending["module"] == "quick_actions"
    assert pending["command"] == "swaymsg exit"