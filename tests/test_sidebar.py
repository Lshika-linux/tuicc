"""Tests for sidebar.py — nav_items and the pure height/preview helpers
(_slot_height, _preview_apps_for). draw() needs a real curses screen to
exercise meaningfully, same as sidebar_compact.py and other modules'
drawing code is left untested here.
"""

from types import SimpleNamespace

from tuicc.model import Region, Window, WMState
from tuicc.navigation import LAST_ITEM_QUERY
from tuicc.modules.sidebar import (
    nav_items, _slot_height, _preview_apps_for, shift_workspace_id,
    _visible_slot_range, _selected_slot_index, _hidden_summary, _windowed_range,
)


def _ctx(regions, total_workspaces=3, selected_id=None, session_preview=None):
    return SimpleNamespace(
        state=WMState(regions=regions),
        config=SimpleNamespace(total_workspaces=total_workspaces),
        selected_id=selected_id,
        session_preview=session_preview,
        typing_mode=False,
        focus_id=None,
    )


def _window(id, app_id):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=(0, 0, 1, 1))


# ---------- _slot_height ----------

def test_slot_height_empty_region_is_two():
    assert _slot_height(None) == 2


def test_slot_height_grows_with_window_count():
    region = Region(id="1", name="1", windows=[_window("w1", "a"), _window("w2", "b")])

    assert _slot_height(region) == 4


def test_slot_height_grows_with_preview_count_too():
    region = Region(id="1", name="1", windows=[_window("w1", "a")])

    assert _slot_height(region, preview_count=2) == 5


def test_slot_height_preview_count_on_empty_region():
    assert _slot_height(None, preview_count=3) == 5


# ---------- _preview_apps_for ----------

def test_preview_apps_for_no_session_preview_returns_empty_list():
    ctx = _ctx(regions=[], session_preview=None)

    assert _preview_apps_for(ctx, "3") == []


def test_preview_apps_for_returns_the_matching_regions_apps():
    ctx = _ctx(regions=[], session_preview={"3": ["kitty", "firefox"], "5": ["obsidian"]})

    assert _preview_apps_for(ctx, "3") == ["kitty", "firefox"]


def test_preview_apps_for_region_with_no_preview_entries_returns_empty_list():
    ctx = _ctx(regions=[], session_preview={"5": ["obsidian"]})

    assert _preview_apps_for(ctx, "3") == []


# ---------- _visible_slot_range / _selected_slot_index (scrolling) ----------
# Budget-based windowing, not slot-count-based like windowed_list.py's
# own window_start() — a workspace slot's own height varies with window
# count, so "fits N items" doesn't apply the same way. See sidebar.py's
# own module docstring for the fuller reasoning.

def test_visible_slot_range_everything_fits_shows_it_all():
    assert _visible_slot_range([2, 2, 2], selected_index=None, available_rows=20) == (0, 3)


def test_visible_slot_range_no_selection_starts_at_zero():
    # available_rows=5 only fits 2 of these 2-row slots
    assert _visible_slot_range([2, 2, 2], selected_index=None, available_rows=5) == (0, 2)


def test_visible_slot_range_selection_forces_scroll_forward():
    # slot 4 (index 3) selected, only 2 rows worth fit at a time (2-row
    # slots) — the newly-scrolled-to selection lands at the BOTTOM of
    # the window (matches window_start()'s own bias), not the top.
    assert _visible_slot_range([2, 2, 2, 2, 2], selected_index=3, available_rows=5) == (2, 4)


def test_visible_slot_range_backfills_when_room_is_left_over():
    # selecting the LAST slot with room for 3: greedy forward finds
    # nothing past it, so it backfills backward instead of showing just
    # the one selected slot alone.
    assert _visible_slot_range([2, 2, 2], selected_index=2, available_rows=6) == (0, 3)


def test_visible_slot_range_variable_heights_respected():
    # slot 0 is tall (6 rows, e.g. many windows); with a budget of 8,
    # only slot 0 + one more 2-row slot fit, not three slots.
    assert _visible_slot_range([6, 2, 2], selected_index=0, available_rows=8) == (0, 2)


def test_visible_slot_range_a_single_slot_taller_than_budget_still_returns_it_alone():
    # An extreme case (one workspace piled with windows) — no crash, no
    # infinite loop, just a 1-slot range that itself still overflows
    # the box (accepted limit, see the function's own docstring).
    assert _visible_slot_range([20, 2, 2], selected_index=0, available_rows=8) == (0, 1)


def test_visible_slot_range_empty_list():
    assert _visible_slot_range([], selected_index=None, available_rows=10) == (0, 0)


def test_selected_slot_index_finds_the_matching_slot():
    slots = [("1", None), ("2", None), ("3", None)]
    assert _selected_slot_index(slots, "sidebar:2") == 1


def test_selected_slot_index_none_when_selection_belongs_to_another_module():
    slots = [("1", None), ("2", None)]
    assert _selected_slot_index(slots, "control:0") is None


def test_selected_slot_index_none_when_nothing_selected():
    slots = [("1", None)]
    assert _selected_slot_index(slots, None) is None


def test_selected_slot_index_last_item_query_returns_the_true_last_index():
    # main.py's Shift+Tab-into-sidebar exception (see LAST_ITEM_QUERY's
    # own docstring in navigation.py) — must be the true last slot, not
    # whatever the default top-anchored window would happen to include.
    slots = [("1", None), ("2", None), ("3", None)]
    assert _selected_slot_index(slots, LAST_ITEM_QUERY) == 2


def test_nav_items_last_item_query_windows_around_the_true_last_slot():
    # End-to-end: even with a box far too short to show everything from
    # the top, querying with LAST_ITEM_QUERY must still return the real
    # final workspace among the results.
    ctx = _ctx(regions=[], total_workspaces=10, selected_id=LAST_ITEM_QUERY)

    items = nav_items((0, 0, 20, 7), ctx, "sidebar")

    assert items[-1].focus_target == "10"


# ---------- nav_items ----------

def test_nav_items_one_per_workspace_slot():
    ctx = _ctx(regions=[], total_workspaces=3)

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    assert [item.focus_target for item in items] == ["1", "2", "3"]


def test_nav_items_use_module_prefixed_ids():
    ctx = _ctx(regions=[], total_workspaces=1)

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    assert items[0].id == "sidebar:1"


def test_nav_items_height_matches_window_count():
    region = Region(id="1", name="1", windows=[_window("w1", "a"), _window("w2", "b")])
    ctx = _ctx(regions=[region], total_workspaces=1)

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    assert items[0].rect[3] == 4  # 2 (base) + 2 windows


def test_nav_items_height_grows_with_a_session_preview():
    # Must match draw()'s own extra rows exactly, or the highlighted/
    # clickable region and what's actually drawn drift apart the moment
    # a slot below this one shifts position.
    region = Region(id="1", name="1", windows=[_window("w1", "a")])
    ctx = _ctx(regions=[region], total_workspaces=1, session_preview={"1": ["kitty", "firefox"]})

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    assert items[0].rect[3] == 5  # 2 (base) + 1 window + 2 preview apps


def test_nav_items_subsequent_slot_offset_accounts_for_preview_height():
    region1 = Region(id="1", name="1", windows=[])
    ctx = _ctx(regions=[region1], total_workspaces=2, session_preview={"1": ["kitty", "firefox", "obsidian"]})

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    # slot 1: base 2 + 3 preview apps = 5 rows tall, starting at y=1
    assert items[0].rect[1] == 1
    assert items[0].rect[3] == 5
    # slot 2 starts right after slot 1's inflated height, not the
    # un-inflated one.
    assert items[1].rect[1] == 6


def test_nav_items_windowed_when_box_too_short_for_everything():
    # 5 empty workspaces (2 rows each = 10 rows) in a box with only
    # h=7 (5 available content rows, after the 2-row border budget
    # subtracted the same way draw() does) — only some slots fit.
    ctx = _ctx(regions=[], total_workspaces=5, selected_id=None)

    items = nav_items((0, 0, 20, 7), ctx, "sidebar")

    # Not all 5 — this is the actual overflow bug being fixed: nav_items()
    # (and draw()) used to return/draw every slot regardless of h.
    assert len(items) < 5


def test_nav_items_scrolls_to_keep_the_selection_visible():
    ctx = _ctx(regions=[], total_workspaces=5, selected_id="sidebar:5")

    items = nav_items((0, 0, 20, 7), ctx, "sidebar")

    assert any(item.id == "sidebar:5" for item in items)


def test_nav_items_peek_item_reaches_the_next_hidden_slot():
    # Selection on slot 1 (topmost), box only fits 2 of 5 slots — Tab
    # forward needs a way to reach slot 3, the next hidden one, same
    # "peek" mechanism windowed_list.py's own consumers (sysmon.py/
    # media.py) already use.
    ctx = _ctx(regions=[], total_workspaces=5, selected_id="sidebar:1")

    items = nav_items((0, 0, 20, 7), ctx, "sidebar")

    assert any(item.focus_target == "3" for item in items)


# ---------- _windowed_range ----------
# Reserves real content rows for "hidden content" indicators, not a
# label embedded in the box's own border — found live, adjacent boxes
# in this codebase's own tightly-packed presets commonly share a
# border row with zero gap, and whichever draws later in MODULES'
# iteration order silently overwrites anything the earlier one drew
# there. See _windowed_range's own docstring for the full story.

def test_windowed_range_no_indicators_needed_when_everything_fits():
    start, end, needs_above, needs_below = _windowed_range([2, 2, 2], None, 20)
    assert (start, end, needs_above, needs_below) == (0, 3, False, False)


def test_windowed_range_below_only_reserves_exactly_one_row():
    # Without reservation, budget=6 fits 3 of these 2-row slots exactly
    # (6/2=3). With one row reserved for the "below" indicator,
    # available drops to 5, fitting only 2.
    start, end, needs_above, needs_below = _windowed_range([2, 2, 2, 2, 2], None, 6)
    assert needs_above is False
    assert needs_below is True
    assert (start, end) == (0, 2)


def test_windowed_range_above_and_below_both_reserve():
    # Selection in the middle, budget too small to reach either edge —
    # both indicators needed, two rows reserved.
    start, end, needs_above, needs_below = _windowed_range([2, 2, 2, 2, 2], 2, 5)
    assert needs_above is True
    assert needs_below is True
    # 5 - 2 reserved = 3 rows left, fits exactly slot 2 alone (2 rows)
    # plus 1 leftover row (not enough for a whole neighboring 2-row slot).
    assert start <= 2 < end


# ---------- _hidden_summary ----------

def test_hidden_summary_empty_indices_is_blank():
    slots = [("1", None, [], 2)]
    assert _hidden_summary(slots, []) == ""


def test_hidden_summary_counts_workspaces_and_windows():
    region = Region(id="2", name="2", windows=[_window("w1", "a"), _window("w2", "b")])
    slots = [("1", None, [], 2), ("2", region, [], 4)]
    assert _hidden_summary(slots, [1]) == "+1 ws, +2 win"


def test_hidden_summary_omits_window_count_when_zero():
    slots = [("1", None, [], 2), ("2", None, [], 2)]
    assert _hidden_summary(slots, [0, 1]) == "+2 ws"


def test_hidden_summary_sums_windows_across_multiple_hidden_workspaces():
    r1 = Region(id="1", name="1", windows=[_window("w1", "a")])
    r2 = Region(id="2", name="2", windows=[_window("w2", "b"), _window("w3", "c")])
    slots = [("1", r1, [], 3), ("2", r2, [], 4)]
    assert _hidden_summary(slots, [0, 1]) == "+2 ws, +3 win"


def test_nav_items_no_peek_items_when_everything_fits():
    ctx = _ctx(regions=[], total_workspaces=3, selected_id=None)

    items = nav_items((0, 0, 20, 20), ctx, "sidebar")

    assert [item.focus_target for item in items] == ["1", "2", "3"]


# ---------- shift_workspace_id ----------
# Up/Down while typing in the launcher move the ambient-typing launch
# target — see main.py's own mode_stack "launcher" tier.

def test_shift_workspace_id_moves_forward():
    assert shift_workspace_id("2", total_workspaces=5, delta=1) == "3"


def test_shift_workspace_id_moves_backward():
    assert shift_workspace_id("2", total_workspaces=5, delta=-1) == "1"


def test_shift_workspace_id_wraps_forward_past_the_last_slot():
    assert shift_workspace_id("5", total_workspaces=5, delta=1) == "1"


def test_shift_workspace_id_wraps_backward_past_the_first_slot():
    assert shift_workspace_id("1", total_workspaces=5, delta=-1) == "5"


def test_shift_workspace_id_none_current_defaults_to_slot_one():
    assert shift_workspace_id(None, total_workspaces=5, delta=1) == "2"


def test_shift_workspace_id_non_numeric_current_defaults_to_slot_one():
    # This codebase's sidebar only ever models numbered 1..total_workspaces
    # slots (see _build_slots) — a non-numeric region id (e.g. a named
    # sway workspace) already doesn't fit that model elsewhere either.
    assert shift_workspace_id("web", total_workspaces=5, delta=1) == "2"


def test_shift_workspace_id_single_workspace_wraps_to_itself():
    assert shift_workspace_id("1", total_workspaces=1, delta=1) == "1"
    assert shift_workspace_id("1", total_workspaces=1, delta=-1) == "1"
