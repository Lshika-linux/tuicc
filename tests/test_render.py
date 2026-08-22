"""Tests for render.py — registry consistency, not drawing (draw_all
needs a real curses screen). The project's own rule is "adding a module
means one line in MODULES and NAV_PROVIDERS, never editing draw_all()" —
these tests catch a violation of that rule (e.g. a module registered in
one dict but forgotten in the other).
"""

from unittest.mock import Mock

import tuicc.render as render
from tuicc.render import MODULES, NAV_PROVIDERS, ACTION_HANDLERS, AUTO_FH_PROVIDERS, apply_auto_fh, draw_all, collect_nav_items
from tuicc.layout import Layout, ModuleBox
from tuicc.modules import power_menu, quick_actions, sessions, control, media


def test_every_module_has_both_draw_and_nav_items():
    assert set(MODULES.keys()) == set(NAV_PROVIDERS.keys())


def test_expected_modules_are_registered():
    expected = {
        "sidebar", "sidebar_compact", "preview", "quick_actions", "rwb", "launcher",
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
    # wifi_network/bluetooth_device/wifi_scan/bluetooth_discover retired
    # along with individual rows' old level-1 reachability — see
    # connectivity.py's own "level-2 browsing" section docstring.
    # Headers now enter browsing instead of scanning directly.
    from tuicc.modules import connectivity
    assert ACTION_HANDLERS["wifi_browse"] is connectivity.handle_wifi_header
    assert ACTION_HANDLERS["bluetooth_browse"] is connectivity.handle_bt_header


def test_sysmon_handlers_registered():
    from tuicc.modules import sysmon
    assert ACTION_HANDLERS["sysmon_row"] is sysmon.handle_row
    assert ACTION_HANDLERS["sysmon_action"] is sysmon.handle_action


# ---------- AUTO_FH_PROVIDERS / apply_auto_fh ----------

def test_auto_fh_providers_registered_for_the_5_list_modules():
    from tuicc.modules import connectivity, rwb
    assert AUTO_FH_PROVIDERS == {
        "control": control.required_fh,
        "connectivity": connectivity.required_fh,
        "media": media.required_fh,
        "sessions": sessions.required_fh,
        "rwb": rwb.required_fh,
    }


def test_apply_auto_fh_recomputes_a_box_with_fh_auto_set(monkeypatch):
    provider = Mock(return_value=9)
    monkeypatch.setattr(render, "AUTO_FH_PROVIDERS", {"fake": provider})
    box = ModuleBox(name="fake", x=0.0, y=0.0, w=0.2, h=None, fh=3, fh_auto=True)
    layout = Layout(boxes=[box])

    apply_auto_fh(layout, cfg="cfg-sentinel")

    assert box.fh == 9
    provider.assert_called_once_with("cfg-sentinel")


def test_apply_auto_fh_leaves_a_manually_frozen_box_alone(monkeypatch):
    provider = Mock(return_value=9)
    monkeypatch.setattr(render, "AUTO_FH_PROVIDERS", {"fake": provider})
    box = ModuleBox(name="fake", x=0.0, y=0.0, w=0.2, h=None, fh=3, fh_auto=False)
    layout = Layout(boxes=[box])

    apply_auto_fh(layout, cfg="cfg-sentinel")

    assert box.fh == 3  # untouched
    provider.assert_not_called()


def test_apply_auto_fh_skips_a_module_with_no_provider(monkeypatch):
    monkeypatch.setattr(render, "AUTO_FH_PROVIDERS", {})
    box = ModuleBox(name="no_provider", x=0.0, y=0.0, w=0.2, h=None, fh=3, fh_auto=True)
    layout = Layout(boxes=[box])

    apply_auto_fh(layout, cfg="cfg-sentinel")  # must not raise

    assert box.fh == 3  # nothing to compute it with, left as-is


# ---------- draw_all/collect_nav_items skip a box collapsed to 0 (see
# layout_engine.py's flexible-box-shrink docstring) ----------

def test_draw_all_skips_a_module_whose_box_has_zero_height(monkeypatch):
    draw_fn = Mock()
    monkeypatch.setattr(render, "MODULES", {"fake": draw_fn})
    layout = Layout(boxes=[ModuleBox(name="fake", x=0.0, y=0.0, w=1.0, h=1.0)])
    boxes = {"fake": (0, 0, 10, 0)}  # h=0 — squeezed out entirely

    draw_all(stdscr=None, layout=layout, boxes=boxes, ctx=None)

    draw_fn.assert_not_called()


def test_draw_all_skips_a_module_whose_box_has_zero_width(monkeypatch):
    draw_fn = Mock()
    monkeypatch.setattr(render, "MODULES", {"fake": draw_fn})
    layout = Layout(boxes=[ModuleBox(name="fake", x=0.0, y=0.0, w=1.0, h=1.0)])
    boxes = {"fake": (0, 0, 0, 10)}

    draw_all(stdscr=None, layout=layout, boxes=boxes, ctx=None)

    draw_fn.assert_not_called()


def test_draw_all_still_draws_a_module_with_a_normal_sized_box(monkeypatch):
    draw_fn = Mock()
    monkeypatch.setattr(render, "MODULES", {"fake": draw_fn})
    layout = Layout(boxes=[ModuleBox(name="fake", x=0.0, y=0.0, w=1.0, h=1.0)])
    boxes = {"fake": (0, 0, 10, 5)}

    draw_all(stdscr="stdscr-sentinel", layout=layout, boxes=boxes, ctx="ctx-sentinel")

    draw_fn.assert_called_once_with("stdscr-sentinel", (0, 0, 10, 5), "ctx-sentinel", "fake")


def test_collect_nav_items_skips_a_module_whose_box_has_zero_height(monkeypatch):
    nav_fn = Mock(return_value=["should never appear"])
    monkeypatch.setattr(render, "NAV_PROVIDERS", {"fake": nav_fn})
    layout = Layout(boxes=[ModuleBox(name="fake", x=0.0, y=0.0, w=1.0, h=1.0)])
    boxes = {"fake": (0, 0, 10, 0)}

    items = collect_nav_items(layout, boxes, ctx=None)

    assert items == []
    nav_fn.assert_not_called()


def test_collect_nav_items_still_collects_from_a_normal_sized_box(monkeypatch):
    nav_fn = Mock(return_value=["item"])
    monkeypatch.setattr(render, "NAV_PROVIDERS", {"fake": nav_fn})
    layout = Layout(boxes=[ModuleBox(name="fake", x=0.0, y=0.0, w=1.0, h=1.0)])
    boxes = {"fake": (0, 0, 10, 5)}

    items = collect_nav_items(layout, boxes, ctx=None)

    assert items == ["item"]
