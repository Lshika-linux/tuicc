"""Tests for render.py — registry consistency, not drawing (draw_all
needs a real curses screen). The project's own rule is "adding a module
means one line in MODULES and NAV_PROVIDERS, never editing draw_all()" —
these tests catch a violation of that rule (e.g. a module registered in
one dict but forgotten in the other).
"""

from tuicc.render import MODULES, NAV_PROVIDERS, ACTION_HANDLERS
from tuicc.modules import power_menu, quick_actions, sessions


def test_every_module_has_both_draw_and_nav_items():
    assert set(MODULES.keys()) == set(NAV_PROVIDERS.keys())


def test_expected_modules_are_registered():
    expected = {
        "sidebar", "sidebar_compact", "preview", "quick_actions", "clock", "launcher",
        "connectivity", "power_menu", "sessions",
    }
    assert set(MODULES.keys()) == expected


def test_power_menu_action_handler_registered():
    assert ACTION_HANDLERS[power_menu.TARGET_KIND] is power_menu.handle


def test_quick_actions_handler_registered():
    assert ACTION_HANDLERS[quick_actions.TARGET_KIND] is quick_actions.handle


def test_sessions_handlers_registered():
    assert ACTION_HANDLERS["session_mode"] is sessions.handle_mode
    assert ACTION_HANDLERS["session_slot"] is sessions.handle_slot


def test_base_handlers_still_present():
    # region/window come from actions.py's BASE_HANDLERS, not any module —
    # confirm they weren't accidentally dropped when other handlers were merged in.
    assert "region" in ACTION_HANDLERS
    assert "window" in ACTION_HANDLERS
