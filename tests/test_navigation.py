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
    next_item_across_modules,
    prev_item_across_modules,
    same_row_neighbor,
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


# ---------- next_item_across_modules / prev_item_across_modules ----------
#
# Regression coverage for a real bug: next_item_in_module
# returning None (module exhausted) used to trigger exactly one
# next_module_name + first_item_in_module lookup — if THAT module also
# had zero items (launcher, preview, and clock all do, in the packaged
# default preset), the keypress did nothing at all, with no way to
# advance further. Power Menu -> Launcher (empty) -> Preview (empty)
# -> Sessions is the exact real sequence that left Tab permanently
# stuck once selection reached Power Menu's last item.

def test_next_item_across_modules_stays_within_module_when_possible():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = next_item_across_modules([a, b], ["sidebar", "preview"], "sidebar", "sidebar:1")

    assert result.id == "sidebar:2"


def test_next_item_across_modules_rolls_into_next_nonempty_module():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="preview:1", rect=(0, 0, 1, 1))

    result = next_item_across_modules([a, b], ["sidebar", "preview"], "sidebar", "sidebar:1")

    assert result.id == "preview:1"


def test_next_item_across_modules_skips_one_empty_module():
    # sidebar exhausted, launcher (next in module_names) has no items
    # in `items` at all, sessions (after that) does.
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sessions:mode:load", rect=(0, 0, 1, 1))

    result = next_item_across_modules(
        [a, b], ["sidebar", "launcher", "sessions"], "sidebar", "sidebar:1"
    )

    assert result.id == "sessions:mode:load"


def test_next_item_across_modules_skips_several_consecutive_empty_modules():
    # The exact real sequence: power_menu exhausted, launcher/preview
    # both empty, sessions is where it should land.
    a = NavItem(id="power_menu:shutdown", rect=(0, 0, 1, 1))
    b = NavItem(id="sessions:mode:load", rect=(0, 0, 1, 1))

    result = next_item_across_modules(
        [a, b],
        ["power_menu", "launcher", "preview", "sessions", "clock"],
        "power_menu",
        "power_menu:shutdown",
    )

    assert result.id == "sessions:mode:load"


def test_next_item_across_modules_wraps_around_module_list():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))

    result = next_item_across_modules([a], ["sidebar", "clock"], "clock", None)

    assert result.id == "sidebar:1"


def test_next_item_across_modules_all_modules_empty_returns_none():
    result = next_item_across_modules([], ["launcher", "preview", "clock"], "launcher", None)

    assert result is None


def test_prev_item_across_modules_stays_within_module_when_possible():
    a = NavItem(id="sidebar:1", rect=(0, 0, 1, 1))
    b = NavItem(id="sidebar:2", rect=(0, 0, 1, 1))

    result = prev_item_across_modules([a, b], ["sidebar", "preview"], "sidebar", "sidebar:2")

    assert result.id == "sidebar:1"


def test_prev_item_across_modules_skips_several_consecutive_empty_modules():
    a = NavItem(id="power_menu:lock", rect=(0, 0, 1, 1))
    b = NavItem(id="sessions:slot:3", rect=(0, 0, 1, 1))

    result = prev_item_across_modules(
        [a, b],
        ["power_menu", "launcher", "preview", "sessions", "clock"],
        "power_menu",
        "power_menu:lock",
    )

    assert result.id == "sessions:slot:3"


def test_prev_item_across_modules_all_modules_empty_returns_none():
    result = prev_item_across_modules([], ["launcher", "preview", "clock"], "launcher", None)

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


# ---------- same_row_neighbor ----------
# The fix for main.py's Left/Right (module_next_keys/module_prev_keys)
# jumping to a whole different module the instant you try to step
# across a module's own horizontal row of items (e.g. sessions.py's
# expanded LOAD/SAVE/DEL/NAME). Left/Right try this first; None (the
# overwhelmingly common case for a single-column module) means they
# fall back to their usual jump-to-module behavior, unaffected.

def _row_item(module, name, x):
    return NavItem(id=f"{module}:{name}", rect=(x, 0.5, 0.05, 0.02))


def test_same_row_neighbor_moves_right_to_the_next_item_on_the_row():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)
    c = _row_item("sessions", "del", 0.3)

    result = same_row_neighbor([a, b, c], selected_id="sessions:load", direction=1)

    assert result is b


def test_same_row_neighbor_moves_left_to_the_previous_item_on_the_row():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)
    c = _row_item("sessions", "del", 0.3)

    result = same_row_neighbor([a, b, c], selected_id="sessions:del", direction=-1)

    assert result is b


def test_same_row_neighbor_returns_none_past_the_last_item():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)

    assert same_row_neighbor([a, b], selected_id="sessions:save", direction=1) is None


def test_same_row_neighbor_returns_none_past_the_first_item():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)

    assert same_row_neighbor([a, b], selected_id="sessions:load", direction=-1) is None


def test_same_row_neighbor_ignores_items_from_a_different_module():
    # Same y, different module — must NOT treat these as row siblings,
    # even though they're geometrically on the same screen row.
    a = _row_item("sessions", "load", 0.1)
    other = _row_item("clock", "time", 0.9)

    assert same_row_neighbor([a, other], selected_id="sessions:load", direction=1) is None


def test_same_row_neighbor_ignores_items_from_a_different_row():
    # Same module, different y — a normal single-column module (every
    # item on its own row) must see this as a no-op, not a match.
    a = NavItem(id="sidebar:1", rect=(0.0, 0.0, 0.1, 0.05))
    b = NavItem(id="sidebar:2", rect=(0.0, 0.1, 0.1, 0.05))

    assert same_row_neighbor([a, b], selected_id="sidebar:1", direction=1) is None


def test_same_row_neighbor_no_selection_returns_none():
    a = _row_item("sessions", "load", 0.1)

    assert same_row_neighbor([a], selected_id=None, direction=1) is None


def test_same_row_neighbor_unknown_selected_id_returns_none():
    a = _row_item("sessions", "load", 0.1)

    assert same_row_neighbor([a], selected_id="sessions:missing", direction=1) is None


# ---------- same_row_neighbor: wrap ----------

def test_same_row_neighbor_wrap_past_the_last_item_returns_the_first():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)
    c = _row_item("sessions", "del", 0.3)

    result = same_row_neighbor([a, b, c], selected_id="sessions:del", direction=1, wrap=True)

    assert result is a


def test_same_row_neighbor_wrap_past_the_first_item_returns_the_last():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)
    c = _row_item("sessions", "del", 0.3)

    result = same_row_neighbor([a, b, c], selected_id="sessions:load", direction=-1, wrap=True)

    assert result is c


def test_same_row_neighbor_wrap_false_by_default_still_returns_none_at_the_edge():
    a = _row_item("sessions", "load", 0.1)
    b = _row_item("sessions", "save", 0.2)

    assert same_row_neighbor([a, b], selected_id="sessions:save", direction=1) is None
