"""Tests for render.py — registry consistency, not drawing (draw_all
needs a real curses screen). The project's own rule is "adding a module
means one line in MODULES and NAV_PROVIDERS, never editing draw_all()" —
these tests catch a violation of that rule (e.g. a module registered in
one dict but forgotten in the other).
"""

from tuicc.render import MODULES, NAV_PROVIDERS, ACTION_HANDLERS
from tuicc.modules import power_menu, quick_actions, sessions, control, media


def test_every_module_has_both_draw_and_nav_items():
    assert set(MODULES.keys()) == set(NAV_PROVIDERS.keys())


def test_expected_modules_are_registered():
    expected = {
        "sidebar", "sidebar_compact", "preview", "quick_actions", "clock", "launcher",
        "connectivity", "power_menu", "sessions", "control", "media", "bars", "sysmon",
    }
    assert set(MODULES.keys()) == expected


def test_power_menu_action_handler_registered():
    assert ACTION_HANDLERS[power_menu.TARGET_KIND] is power_menu.handle


def test_quick_actions_handler_registered():
    assert ACTION_HANDLERS[quick_actions.TARGET_KIND] is quick_actions.handle


def test_control_toggle_handler_registered():
    assert ACTION_HANDLERS[control.TARGET_KIND] is control.handle


def test_sessions_handlers_registered():
    assert ACTION_HANDLERS["session_row"] is sessions.handle_row
    assert ACTION_HANDLERS["session_action"] is sessions.handle_action


def test_media_handlers_registered():
    assert ACTION_HANDLERS["media_row"] is media.handle_row
    assert ACTION_HANDLERS["media_transport"] is media.handle_transport
    assert ACTION_HANDLERS["media_output"] is media.handle_output


def test_base_handlers_still_present():
    # region/window come from actions.py's BASE_HANDLERS, not any module —
    # confirm they weren't accidentally dropped when other handlers were merged in.
    assert "region" in ACTION_HANDLERS
    assert "window" in ACTION_HANDLERS


def test_connectivity_handlers_registered():
    from tuicc.modules import connectivity
    assert ACTION_HANDLERS["wifi_network"] is connectivity.handle_wifi
    assert ACTION_HANDLERS["bluetooth_device"] is connectivity.handle_bluetooth
    assert ACTION_HANDLERS["wifi_scan"] is connectivity.handle_wifi_scan
    assert ACTION_HANDLERS["bluetooth_discover"] is connectivity.handle_bluetooth_discover


def test_sysmon_handlers_registered():
    from tuicc.modules import sysmon
    assert ACTION_HANDLERS["sysmon_row"] is sysmon.handle_row
    assert ACTION_HANDLERS["sysmon_action"] is sysmon.handle_action
