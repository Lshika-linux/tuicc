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
    _visible_slot_range, _selected_slot_index, _hidden_summary,
    _fitting_title, _right_aligned_overlay_col, _grouped_window_rows,
    slot_ids, _build_slots,
)
from tuicc.wm_config_parser import WmConfigInfo


def _cfg():
    return SimpleNamespace(terminal_apps=set(), browser_apps=set(), browser_title_names=set())


def _ctx(regions, total_workspaces=3, selected_id=None, session_preview=None, wm_config=None):
    return SimpleNamespace(
        state=WMState(regions=regions),
        config=SimpleNamespace(
            total_workspaces=total_workspaces, workspace_mode="autodetect", workspace_names=None,
            **vars(_cfg()),
        ),
        selected_id=selected_id,
        session_preview=session_preview,
        typing_mode=False,
        focus_id=None,
        wm_config=wm_config,
    )


def _window(id, app_id, title="", tab_group_id=None, tab_group_layout=None):
    return Window(
        id=id, app_id=app_id, title=title, focused=False, rect=(0, 0, 1, 1),
        tab_group_id=tab_group_id, tab_group_layout=tab_group_layout,
    )


# ---------- _slot_height ----------

def test_slot_height_empty_region_is_two():
    assert _slot_height(None, _cfg()) == 2


def test_slot_height_grows_with_window_count():
    region = Region(id="1", name="1", windows=[_window("w1", "a"), _window("w2", "b")])

    assert _slot_height(region, _cfg()) == 4


def test_slot_height_grows_with_preview_count_too():
    region = Region(id="1", name="1", windows=[_window("w1", "a")])

    assert _slot_height(region, _cfg(), preview_count=2) == 5


def test_slot_height_preview_count_on_empty_region():
    assert _slot_height(None, _cfg(), preview_count=3) == 5


def test_slot_height_collapses_identical_windows_into_one_row():
    # Two "a" windows with the same (empty) title collapse into a
    # single row — see _grouped_window_rows()'s own docstring. Height
    # reflects the collapsed row count (1), not the raw window count
    # (2): base 2 + 1 distinct row = 3, not 4.
    region = Region(id="1", name="1", windows=[_window("w1", "a"), _window("w2", "a")])

    assert _slot_height(region, _cfg()) == 3


# ---------- _grouped_window_rows ----------

def test_grouped_window_rows_no_duplicates_stays_one_row_per_window():
    windows = [_window("w1", "a"), _window("w2", "b")]

    assert _grouped_window_rows(windows, _cfg()) == [("a", "", 1, None), ("b", "", 1, None)]


def test_grouped_window_rows_collapses_exact_duplicates_with_a_count():
    windows = [_window("w1", "kitty"), _window("w2", "kitty"), _window("w3", "kitty")]

    assert _grouped_window_rows(windows, _cfg()) == [("kitty", "", 3, None)]


def test_grouped_window_rows_different_titles_stay_separate_even_for_the_same_app():
    # Same app_id, but condense_title() produces a genuinely different
    # detail for each (a real title, not the empty default) — must NOT
    # collapse together, or real distinguishing info (what's actually
    # running) would silently vanish.
    cfg = SimpleNamespace(terminal_apps={"kitty"}, browser_apps=set(), browser_title_names=set())
    windows = [_window("w1", "kitty", title="htop"), _window("w2", "kitty", title="nvim main.py")]

    result = _grouped_window_rows(windows, cfg)

    assert len(result) == 2
    assert all(count == 1 for _app, _detail, count, _layout in result)


def test_grouped_window_rows_mixed_some_duplicate_some_not():
    windows = [_window("w1", "kitty"), _window("w2", "kitty"), _window("w3", "firefox")]

    assert _grouped_window_rows(windows, _cfg()) == [("kitty", "", 2, None), ("firefox", "", 1, None)]


def test_grouped_window_rows_preserves_first_seen_order():
    windows = [_window("w1", "c"), _window("w2", "a"), _window("w3", "c"), _window("w4", "b")]

    result = _grouped_window_rows(windows, _cfg())

    assert [app for app, _detail, _count, _layout in result] == ["c", "a", "b"]


def test_grouped_window_rows_same_app_and_detail_but_different_group_stay_separate():
    # GitHub issue #8 follow-up, found live (2026-08-31): 8 otherwise-
    # identical bare "kitty" shells, 7 sitting in one real stacked
    # group and 1 sitting completely alone elsewhere on the workspace
    # — collapsing all 8 into one "×8" count would misrepresent that as
    # one uniform thing when it's really two.
    grouped = [_window(f"w{i}", "kitty", tab_group_id="9", tab_group_layout="stacked") for i in range(7)]
    lone = _window("w7", "kitty")
    windows = grouped + [lone]

    result = _grouped_window_rows(windows, _cfg())

    assert result == [("kitty", "", 7, "S1"), ("kitty", "", 1, None)]


def test_grouped_window_rows_two_distinct_stacked_groups_get_their_own_number():
    # A workspace with TWO separate stacked containers, side by side —
    # each needs its own tellable-apart label, not both just "stacked".
    a = [_window(f"a{i}", "kitty", tab_group_id="1", tab_group_layout="stacked") for i in range(2)]
    b = [_window(f"b{i}", "kitty", tab_group_id="2", tab_group_layout="stacked") for i in range(3)]

    result = _grouped_window_rows(a + b, _cfg())

    assert result == [("kitty", "", 2, "S1"), ("kitty", "", 3, "S2")]


def test_grouped_window_rows_stacked_and_tabbed_groups_number_independently():
    # A stacked group and a tabbed group both present — each layout
    # gets its OWN counter, so this is "S1"/"T1", not
    # "S1"/"S2" (they're not the same kind of thing).
    stacked = [_window(f"s{i}", "kitty", tab_group_id="1", tab_group_layout="stacked") for i in range(2)]
    tabbed = [_window(f"t{i}", "kitty", tab_group_id="2", tab_group_layout="tabbed", title="x") for i in range(2)]

    result = _grouped_window_rows(stacked + tabbed, _cfg())

    labels = {label for _app, _detail, _count, label in result}
    assert labels == {"S1", "T1"}


def test_grouped_window_rows_ungrouped_window_reports_no_label():
    windows = [_window("w1", "kitty")]

    assert _grouped_window_rows(windows, _cfg()) == [("kitty", "", 1, None)]


def test_grouped_window_rows_reorders_so_group_rows_stay_adjacent():
    # Rafi's own live ask (2026-08-31): a group's rows scattered among
    # ordinary windows (whatever order region.windows happens to
    # report) made the group hard to spot as one thing at a glance —
    # this builds that exact scattered input and checks the OUTPUT
    # order groups everything together instead.
    ungrouped_first = _window("u1", "firefox")
    group_a = _window("g1", "kitty", title="impala", tab_group_id="1", tab_group_layout="stacked")
    ungrouped_middle = _window("u2", "code")
    group_b = _window("g2", "kitty", title="htop", tab_group_id="1", tab_group_layout="stacked")
    windows = [ungrouped_first, group_a, ungrouped_middle, group_b]

    result = _grouped_window_rows(windows, SimpleNamespace(terminal_apps={"kitty"}, browser_apps=set(), browser_title_names=set()))

    # Both group_a/group_b rows (same group_id) come first, adjacent
    # to each other, in their own first-seen order; the two ungrouped
    # rows follow, also keeping their own first-seen relative order.
    apps_and_details = [(app, detail) for app, detail, _count, _label in result]
    assert apps_and_details == [("kitty", "impala"), ("kitty", "htop"), ("firefox", ""), ("code", "")]
    assert [label for _a, _d, _c, label in result] == ["S1", "S1", None, None]


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


# ---------- _fitting_title (TOP row) ----------
# Sidebar OWNS the top row's own title outright (Sessions, whatever's
# above it in the default preset, never puts real content on the
# shared row) — safe to fully redraw combined with "Workspaces",
# degrading gracefully by dropping it when both don't fit. Found live:
# "Workspaces +7 ws, +11 win" routinely doesn't fit a sidebar-width
# box, and draw_box_outline's own fallback for a title that doesn't
# fit is BLANK dashes — no title at all, not even bare "Workspaces".

def test_fitting_title_uses_full_combo_when_it_fits():
    assert _fitting_title("Workspaces", "+7 ws, +11 win", 40) == "Workspaces +7 ws, +11 win"


def test_fitting_title_drops_base_when_only_indicator_fits():
    assert _fitting_title("Workspaces", "+7 ws, +11 win", 20) == "+7 ws, +11 win"


def test_fitting_title_falls_back_to_bare_base_when_only_that_fits():
    assert _fitting_title("Workspaces", "+7 ws, +11 win", 15) == "Workspaces"


def test_fitting_title_returns_base_even_when_nothing_fits_at_all():
    # Still better than draw_box_outline's own blank-dashes fallback —
    # box identity survives even when the extra info can't.
    assert _fitting_title("Workspaces", "+7 ws, +11 win", 3) == "Workspaces"


# ---------- _right_aligned_overlay_col (BOTTOM row) ----------
# The bottom row is different from the top: whatever's directly below
# (Control, in the default preset) has its OWN real title there, which
# a combined redraw would silently clobber (confirmed live: an earlier
# version chopped "Control" mid-word). This is an OVERLAY instead —
# writes only its own cells, right-aligned, never touching what's
# already on that row — so it only ever needs to answer "where does MY
# OWN text start", given a caller-supplied left margin.

def test_right_aligned_overlay_col_fits_with_room_to_spare():
    assert _right_aligned_overlay_col(0, 20, " +N ", min_left_col=5) == 14


def test_right_aligned_overlay_col_none_when_it_would_collide_with_the_left_side():
    # min_left_col here stands in for the conservative margin
    # draw_hidden_indicators() reserves for whatever's below.
    assert _right_aligned_overlay_col(0, 20, " +N ", min_left_col=15) is None


def test_right_aligned_overlay_col_respects_x_offset():
    assert _right_aligned_overlay_col(100, 20, " +N ", min_left_col=100) == 114


# ---------- _hidden_summary ----------
# Feeds sidebar.draw_hidden_indicators() — a second, later border-only
# redraw (see that function's own docstring for why it has to be a
# separate, later pass rather than reserving content rows: found live,
# this codebase's own tightly-packed presets commonly place another
# module's box directly adjacent with zero gap, sharing the exact
# border row, and whichever module draws later in MODULES' own
# iteration order silently overwrites anything drawn there during the
# normal per-module pass). draw_hidden_indicators() itself needs a real
# curses screen to exercise meaningfully, same as draw() — left
# untested here; _hidden_summary is the pure logic it's built on.

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


# ---------- slot_ids ----------
# GitHub issue #9: real region ids used to be silently invisible the
# instant they fell outside "1".."total_workspaces" (a named workspace,
# or any number past total_workspaces). See wm_config_parser.py for
# where wm_config itself comes from.

def _region(id):
    return Region(id=id, name=id, windows=[], focused=False)


def test_slot_ids_falls_back_to_numeric_range_without_wm_config():
    assert slot_ids([], wm_config=None, total_workspaces=3) == ["1", "2", "3"]


def test_slot_ids_falls_back_to_numeric_range_when_wm_config_has_no_names():
    assert slot_ids([], wm_config=WmConfigInfo(), total_workspaces=3) == ["1", "2", "3"]


def test_slot_ids_uses_wm_config_names_when_present():
    wm_config = WmConfigInfo(workspace_names=["10", "20", "chat"])
    assert slot_ids([], wm_config, total_workspaces=3) == ["10", "20", "chat"]


def test_slot_ids_manual_mode_uses_manual_names_even_with_wm_config_present():
    # manual outranks autodetect entirely when explicitly chosen -
    # config.py already refuses to load "manual" with an empty list,
    # so manual_workspace_names being real is a given whenever this
    # branch is taken.
    wm_config = WmConfigInfo(workspace_names=["10", "20"])
    ids = slot_ids(
        [], wm_config, total_workspaces=3,
        workspace_mode="manual", manual_workspace_names=["a", "b"],
    )
    assert ids == ["a", "b"]


def test_slot_ids_manual_mode_still_unions_real_regions():
    regions = [_region("c")]
    ids = slot_ids(
        regions, wm_config=None, total_workspaces=3,
        workspace_mode="manual", manual_workspace_names=["a", "b"],
    )
    assert ids == ["a", "b", "c"]


def test_slot_ids_unions_real_regions_not_covered_by_the_base_list():
    # The actual fix: a real region outside the base list (whichever
    # source produced it) is never hidden, just appended.
    regions = [_region("20")]
    assert slot_ids(regions, wm_config=None, total_workspaces=3) == ["1", "2", "3", "20"]


def test_slot_ids_real_regions_already_covered_are_not_duplicated():
    wm_config = WmConfigInfo(workspace_names=["10", "20"])
    regions = [_region("20")]
    assert slot_ids(regions, wm_config, total_workspaces=3) == ["10", "20"]


def test_slot_ids_named_workspaces_survive_unioning():
    regions = [_region("web"), _region("chat")]
    assert slot_ids(regions, wm_config=None, total_workspaces=2) == ["1", "2", "web", "chat"]


def test_slot_ids_numbered_named_region_merges_not_duplicates():
    # Found live: a real region's own id is always the bare workspace
    # number (providers/sway.py's parse_tree()), so "8" and a
    # wm_config-parsed "8:VIII" used to be treated as two unrelated
    # slots — every populated numbered+named workspace showed twice.
    wm_config = WmConfigInfo(workspace_names=["1:I", "8:VIII", "9:IX"])
    regions = [_region("8")]
    ids = slot_ids(regions, wm_config, total_workspaces=3)
    assert ids == ["1:I", "8:VIII", "9:IX"]  # one slot, not four


def test_slot_ids_manual_mode_numbered_named_region_also_merges():
    regions = [_region("8")]
    ids = slot_ids(
        regions, wm_config=None, total_workspaces=3,
        workspace_mode="manual", manual_workspace_names=["1:I", "8:VIII"],
    )
    assert ids == ["1:I", "8:VIII"]


def test_build_slots_merged_slot_carries_the_real_region_not_none():
    # The other half of the fix: slot_ids() merging "8" into "8:VIII"
    # is only useful if _build_slots()'s own by_id lookup ALSO finds
    # the real region under that same resolved key — otherwise the
    # merged slot would render as if it were still empty.
    region = Region(id="8", name="8", windows=[_window("w1", "kitty")])
    wm_config = WmConfigInfo(workspace_names=["1:I", "8:VIII"])
    ctx = _ctx([region], total_workspaces=3, wm_config=wm_config)

    slots = _build_slots(ctx)

    assert slots == [("1:I", None), ("8:VIII", region)]


# ---------- shift_workspace_id ----------
# Up/Down while typing in the launcher move the ambient-typing launch
# target — see main.py's own mode_stack "launcher" tier. GitHub issue
# #9: ids is now the real slot_ids() list (any real WM-declared name),
# not blind "1".."total_workspaces" modulo arithmetic.

_IDS5 = ["1", "2", "3", "4", "5"]


def test_shift_workspace_id_moves_forward():
    assert shift_workspace_id("2", _IDS5, delta=1) == "3"


def test_shift_workspace_id_moves_backward():
    assert shift_workspace_id("2", _IDS5, delta=-1) == "1"


def test_shift_workspace_id_wraps_forward_past_the_last_slot():
    assert shift_workspace_id("5", _IDS5, delta=1) == "1"


def test_shift_workspace_id_wraps_backward_past_the_first_slot():
    assert shift_workspace_id("1", _IDS5, delta=-1) == "5"


def test_shift_workspace_id_none_current_defaults_to_first_slot():
    assert shift_workspace_id(None, _IDS5, delta=1) == "1"


def test_shift_workspace_id_current_not_in_ids_defaults_to_last_slot_going_backward():
    assert shift_workspace_id("nonexistent", _IDS5, delta=-1) == "5"


def test_shift_workspace_id_named_workspace_now_works_normally():
    # The actual fix this issue was about: a non-numeric, real
    # WM-declared workspace name is just another entry in ids now, not
    # a case that falls through to a fallback at all.
    ids = ["1", "web", "chat"]
    assert shift_workspace_id("web", ids, delta=1) == "chat"
    assert shift_workspace_id("web", ids, delta=-1) == "1"


def test_shift_workspace_id_single_workspace_wraps_to_itself():
    assert shift_workspace_id("1", ["1"], delta=1) == "1"
    assert shift_workspace_id("1", ["1"], delta=-1) == "1"


def test_shift_workspace_id_empty_ids_returns_current_unchanged():
    assert shift_workspace_id("1", [], delta=1) == "1"
