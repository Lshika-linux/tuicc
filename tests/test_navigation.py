"""Tests for navigation.py — all pure functions over NavItem lists,
no WM connection or curses screen needed.
"""

import pytest

from tuicc.navigation import (
    NavItem,
    tab_order,
    nearest_in_direction,
    hotkey_map,
    sibling_in_same_group,
    region_item_for_focus,
    module_of_item,
    first_item_in_module,
    resolve_direction_move,
    global_shortcut_item,
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


# ---------- nearest_in_direction ----------

def test_nearest_in_direction_basic_right():
    current = NavItem(id="current", rect=(0.0, 0.0, 0.1, 0.1))
    near = NavItem(id="near", rect=(0.2, 0.0, 0.1, 0.1))
    far = NavItem(id="far", rect=(0.6, 0.0, 0.1, 0.1))

    result = nearest_in_direction([current, near, far], current, "right")

    assert result.id == "near"


def test_nearest_in_direction_no_candidates_returns_none():
    current = NavItem(id="current", rect=(0.5, 0.5, 0.1, 0.1))
    only_to_the_left = NavItem(id="left", rect=(0.0, 0.5, 0.1, 0.1))

    result = nearest_in_direction([current, only_to_the_left], current, "right")

    assert result is None


def test_nearest_in_direction_overlap_beats_closer_non_overlap():
    """Documents a known tradeoff (see Architecture wiki): a large item
    that overlaps the current item's row/column always wins over a
    smaller, geometrically closer item that doesn't overlap at all —
    this is exactly why preview windows use sibling_in_same_group
    instead of this function, rather than tuning the scoring further.
    """
    current = NavItem(id="current", rect=(0.0, 0.0, 0.1, 0.5))
    big_overlapping_but_far = NavItem(id="big_far", rect=(0.5, 0.0, 0.1, 0.5))
    small_close_no_overlap = NavItem(id="small_near", rect=(0.15, 0.6, 0.1, 0.1))

    result = nearest_in_direction(
        [current, big_overlapping_but_far, small_close_no_overlap], current, "right"
    )

    assert result.id == "big_far"


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


# ---------- sibling_in_same_group ----------

def test_sibling_in_same_group_right():
    left = NavItem(id="left", rect=(0.0, 0.0, 0.1, 0.1), target_kind="window")
    mid = NavItem(id="mid", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    right = NavItem(id="right", rect=(0.6, 0.0, 0.1, 0.1), target_kind="window")

    result = sibling_in_same_group([left, mid, right], mid, "right")

    assert result.id == "right"


def test_sibling_in_same_group_left():
    left = NavItem(id="left", rect=(0.0, 0.0, 0.1, 0.1), target_kind="window")
    mid = NavItem(id="mid", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    right = NavItem(id="right", rect=(0.6, 0.0, 0.1, 0.1), target_kind="window")

    result = sibling_in_same_group([left, mid, right], mid, "left")

    assert result.id == "left"


def test_sibling_in_same_group_boundary_returns_none():
    left = NavItem(id="left", rect=(0.0, 0.0, 0.1, 0.1), target_kind="window")
    right = NavItem(id="right", rect=(0.6, 0.0, 0.1, 0.1), target_kind="window")

    result = sibling_in_same_group([left, right], left, "left")

    assert result is None


def test_sibling_in_same_group_ignores_other_target_kinds():
    window = NavItem(id="window", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    region_to_the_right = NavItem(id="region", rect=(0.6, 0.0, 0.1, 0.1), target_kind="region")

    result = sibling_in_same_group([window, region_to_the_right], window, "right")

    assert result is None


# ---------- region_item_for_focus ----------

def test_region_item_for_focus_finds_match():
    region1 = NavItem(id="sidebar:1", rect=(0, 0, 1, 1), focus_target="1", target_kind="region")
    region2 = NavItem(id="sidebar:2", rect=(0, 1, 1, 1), focus_target="2", target_kind="region")

    result = region_item_for_focus([region1, region2], "2")

    assert result.id == "sidebar:2"


def test_region_item_for_focus_no_match_returns_none():
    region1 = NavItem(id="sidebar:1", rect=(0, 0, 1, 1), focus_target="1", target_kind="region")

    result = region_item_for_focus([region1], "99")

    assert result is None


# ---------- module_of_item ----------

def test_module_of_item():
    item = NavItem(id="sidebar:3", rect=(0, 0, 1, 1))

    assert module_of_item(item) == "sidebar"


def test_module_of_item_preview_window():
    item = NavItem(id="preview:12345", rect=(0, 0, 1, 1))

    assert module_of_item(item) == "preview"


# ---------- first_item_in_module ----------

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


# ---------- resolve_direction_move ----------

def test_resolve_direction_move_prefers_sibling_over_spatial():
    left = NavItem(id="preview:left", rect=(0.0, 0.0, 0.1, 0.1), target_kind="window")
    mid = NavItem(id="preview:mid", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    right = NavItem(id="preview:right", rect=(0.6, 0.0, 0.1, 0.1), target_kind="window")

    result = resolve_direction_move([left, mid, right], mid, "right", focus_id=None)

    assert result.id == "preview:right"


def test_resolve_direction_move_left_boundary_falls_back_to_region():
    leftmost = NavItem(id="preview:leftmost", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    region = NavItem(id="sidebar:1", rect=(0, 0, 0.1, 0.1), focus_target="1", target_kind="region")

    result = resolve_direction_move([leftmost, region], leftmost, "left", focus_id="1")

    assert result.id == "sidebar:1"


def test_resolve_direction_move_left_boundary_no_region_match_falls_back_to_spatial():
    leftmost = NavItem(id="preview:leftmost", rect=(0.3, 0.0, 0.1, 0.1), target_kind="window")
    far_left_region = NavItem(id="sidebar:1", rect=(0.0, 0.0, 0.1, 0.1), focus_target="1", target_kind="region")

    result = resolve_direction_move([leftmost, far_left_region], leftmost, "left", focus_id="99")

    assert result.id == "sidebar:1"


def test_resolve_direction_move_non_window_skips_straight_to_spatial():
    region = NavItem(id="sidebar:1", rect=(0.0, 0.0, 0.1, 0.1), target_kind="region")
    other_region = NavItem(id="sidebar:2", rect=(0.0, 0.2, 0.1, 0.1), target_kind="region")

    result = resolve_direction_move([region, other_region], region, "down", focus_id=None)

    assert result.id == "sidebar:2"


def test_resolve_direction_move_up_down_skips_sibling_search():
    """sibling_in_same_group only understands left/right — up/down for a
    window item must go straight to spatial search, not silently match
    a same-group item via the sibling path.
    """
    top = NavItem(id="preview:top", rect=(0.0, 0.0, 0.1, 0.1), target_kind="window")
    bottom = NavItem(id="preview:bottom", rect=(0.0, 0.3, 0.1, 0.1), target_kind="window")

    result = resolve_direction_move([top, bottom], top, "down", focus_id=None)

    assert result.id == "preview:bottom"


def test_resolve_direction_move_no_candidates_returns_none():
    lone = NavItem(id="preview:lone", rect=(0.5, 0.5, 0.1, 0.1), target_kind="window")

    result = resolve_direction_move([lone], lone, "right", focus_id=None)

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
