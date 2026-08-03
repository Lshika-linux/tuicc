"""Tests for navigation.py — all pure functions over NavItem lists,
no WM connection or curses screen needed.
"""

import pytest

from tuicc.navigation import (
    NavItem,
    tab_order,
    hotkey_map,
    module_of_item,
    first_item_in_module,
    last_item_in_module,
    resolve_selection,
    global_shortcut_item,
    next_module_name,
    prev_module_name,
    next_item_in_module,
    prev_item_in_module,
)


# ---------- tab_order ----------

def test_tab_order_columns_first():
    a = NavItem(id="a", rect=(0.5, 0.0, 0.1, 0.1))
    b = NavItem(id="b", rect=(0.0, 0.5, 0.1, 0.1))
    c = NavItem(id="c", rect=(0.0, 0.0, 0.1, 0.1))

    result = tab_order([a, b, c], mode="columns_first")

    assert [item.id for item in result] == ["c", "b", "a"]


def test_tab_order_rows_first():
    a = NavItem(id="a", rect=(0.5, 0.0, 0.1, 0.1))
    b = NavItem(id="b", rect=(0.0, 0.5, 0.1, 0.1))
    c = NavItem(id="c", rect=(0.0, 0.0, 0.1, 0.1))

    result = tab_order([a, b, c], mode="rows_first")

    assert [item.id for item in result] == ["c", "a", "b"]


def test_tab_order_invalid_mode_raises():
    with pytest.raises(ValueError):
        tab_order([], mode="diagonal_first")


# ---------- hotkey_map ----------

def test_hotkey_map_builds_dict():
    a = NavItem(id="a", rect=(0, 0, 1, 1), hotkey="1")
    b = NavItem(id="b", rect=(0, 0, 1, 1), hotkey="2")
    no_hotkey = NavItem(id="c", rect=(0, 0, 1, 1))

    result = hotkey_map([a, b, no_hotkey])

    assert result == {"1": a, "2": b}


def test_hotkey_map_duplicate_raises():
    a = NavItem(id="a", rect=(0, 0, 1, 1), hotkey="1")
    b = NavItem(id="b", rect=(0, 0, 1, 1), hotkey="1")

    with pytest.raises(ValueError):
        hotkey_map([a, b])


# ---------- module_of_item ----------

def test_module_of_item():
    item = NavItem(id="sidebar:3", rect=(0, 0, 1, 1))

    assert module_of_item(item) == "sidebar"


def test_module_of_item_preview_window():
    item = NavItem(id="preview:12345", rect=(0, 0, 1, 1))

    assert module_of_item(item) == "preview"


# ---------- first_item_in_module / last_item_in_module ----------

def test_first_item_in_module_returns_first_match():
    sidebar_item = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))
    another_sidebar_item = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = first_item_in_module([sidebar_item, preview_item, another_sidebar_item], "sidebar")

    assert result.id == "sidebar:1"


def test_first_item_in_module_no_match_returns_none():
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))

    result = first_item_in_module([preview_item], "quick_actions")

    assert result is None


def test_last_item_in_module_returns_last_match():
    sidebar_item = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))
    another_sidebar_item = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = last_item_in_module([sidebar_item, preview_item, another_sidebar_item], "sidebar")

    assert result.id == "sidebar:2"


def test_last_item_in_module_no_match_returns_none():
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))

    result = last_item_in_module([preview_item], "quick_actions")

    assert result is None


# ---------- global_shortcut_item ----------

def test_global_shortcut_item_builds_navitem_for_bound_key():
    global_shortcuts = {
        ord("l"): {"item_id": "power_menu:lock", "target_kind": "power_action"},
    }

    result = global_shortcut_item(global_shortcuts, ord("l"))

    assert result.id == "power_menu:lock"
    assert result.target_kind == "power_action"
    assert result.rect == (0, 0, 0, 0)
    assert result.focus_target is None


def test_global_shortcut_item_unbound_key_returns_none():
    global_shortcuts = {
        ord("l"): {"item_id": "power_menu:lock", "target_kind": "power_action"},
    }

    result = global_shortcut_item(global_shortcuts, ord("x"))

    assert result is None


def test_global_shortcut_item_empty_shortcuts_returns_none():
    result = global_shortcut_item({}, ord("l"))

    assert result is None


# ---------- next_module_name / prev_module_name ----------

def test_next_module_name_cycles_forward():
    result = next_module_name(["sidebar", "preview", "launcher"], "sidebar")

    assert result == "preview"


def test_next_module_name_wraps_around():
    result = next_module_name(["sidebar", "preview", "launcher"], "launcher")

    assert result == "sidebar"


def test_next_module_name_unknown_active_starts_from_first():
    result = next_module_name(["sidebar", "preview"], "some_unregistered_module")

    assert result == "preview"


def test_next_module_name_empty_list_returns_none():
    result = next_module_name([], "sidebar")

    assert result is None


def test_prev_module_name_cycles_backward():
    result = prev_module_name(["sidebar", "preview", "launcher"], "preview")

    assert result == "sidebar"


def test_prev_module_name_wraps_around():
    result = prev_module_name(["sidebar", "preview", "launcher"], "sidebar")

    assert result == "launcher"


def test_prev_module_name_unknown_active_starts_from_first():
    # index 0 - 1 wraps to the last name, same "starts from a defined
    # place" contract as next_module_name, just the backward direction
    # of it.
    result = prev_module_name(["sidebar", "preview"], "some_unregistered_module")

    assert result == "preview"


def test_prev_module_name_empty_list_returns_none():
    result = prev_module_name([], "sidebar")

    assert result is None


# ---------- next_item_in_module / prev_item_in_module ----------

def test_next_item_in_module_cycles_forward():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))
    c = NavItem(id="sidebar:3", rect=(0, 0, 1, 1))

    result = next_item_in_module([a, b, c], "sidebar", "sidebar:1")

    assert result.id == "sidebar:2"


def test_next_item_in_module_no_wrap_returns_none_at_last_item():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = next_item_in_module([a, b], "sidebar", "sidebar:2")

    assert result is None


def test_next_item_in_module_ignores_other_modules():
    sidebar_item = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))
    another_sidebar_item = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = next_item_in_module([sidebar_item, preview_item, another_sidebar_item], "sidebar", "sidebar:1")

    assert result.id == "sidebar:2"


def test_next_item_in_module_unknown_selected_starts_from_first():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = next_item_in_module([a, b], "sidebar", selected_id=None)

    assert result.id == "sidebar:2"


def test_next_item_in_module_no_items_returns_none():
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))

    result = next_item_in_module([preview_item], "sidebar", "sidebar:1")

    assert result is None


def test_prev_item_in_module_cycles_backward():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))
    c = NavItem(id="sidebar:3", rect=(0, 0, 1, 1))

    result = prev_item_in_module([a, b, c], "sidebar", "sidebar:3")

    assert result.id == "sidebar:2"


def test_prev_item_in_module_no_wrap_returns_none_at_first_item():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = prev_item_in_module([a, b], "sidebar", "sidebar:1")

    assert result is None


def test_prev_item_in_module_ignores_other_modules():
    sidebar_item = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))
    another_sidebar_item = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = prev_item_in_module([sidebar_item, preview_item, another_sidebar_item], "sidebar", "sidebar:2")

    assert result.id == "sidebar:1"


def test_prev_item_in_module_no_items_returns_none():
    preview_item = NavItem(id="preview:1", rect=(0, 0, 1, 1))

    result = prev_item_in_module([preview_item], "sidebar", "sidebar:1")

    assert result is None


# ---------- resolve_selection ----------

def test_resolve_selection_region_item_updates_focus_id():
    item = NavItem(id="sidebar:2", rect=(0, 0, 1, 1), focus_target="2", target_kind="region")

    selected_id, active_module, focus_id = resolve_selection(item, focus_id="1")

    assert selected_id == "sidebar:2"
    assert active_module == "sidebar"
    assert focus_id == "2"


def test_resolve_selection_non_region_item_keeps_focus_id_unchanged():
    item = NavItem(id="preview:window-5", rect=(0, 0, 1, 1), target_kind="window")

    selected_id, active_module, focus_id = resolve_selection(item, focus_id="1")

    assert selected_id == "preview:window-5"
    assert active_module == "preview"
    assert focus_id == "1"
