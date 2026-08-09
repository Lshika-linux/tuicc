"""Tests for windowed_list.py's pure "fixed N-visible-slots + peek nav
items" mechanic — extracted out of test_media_module.py (see
windowed_list.py's own docstring for the extraction reasoning).
modules/media.py's own tests still cover the integration behavior
(that _build_rows/nav_items actually use this correctly for Now
Playing/Output specifically) — this file only covers the generic
functions themselves.
"""

from tuicc.windowed_list import window_start, section_nav_indices, section_rows, header_with_count


# ---------- window_start ----------

def test_window_start_no_scroll_needed_when_everything_fits():
    assert window_start(count=2, selected_index=1) == 0
    assert window_start(count=3, selected_index=2) == 0


def test_window_start_no_selection_starts_at_zero():
    assert window_start(count=10, selected_index=None) == 0


def test_window_start_selection_within_first_window_stays_at_zero():
    assert window_start(count=10, selected_index=0) == 0
    assert window_start(count=10, selected_index=2) == 0


def test_window_start_scrolls_minimally_to_include_selection():
    # selecting index 3 (the 4th item) with 3 visible slots must bring
    # it into view as the LAST visible slot, not centered/over-scrolled
    assert window_start(count=10, selected_index=3) == 1
    assert window_start(count=10, selected_index=9) == 7


def test_window_start_never_scrolls_past_the_end():
    assert window_start(count=5, selected_index=4, visible_slots=3) == 2  # not 4 or more


# ---------- section_nav_indices ----------

def test_section_nav_indices_none_when_everything_fits():
    assert section_nav_indices(count=3, selected_index=None) == (None, None)
    assert section_nav_indices(count=0, selected_index=None) == (None, None)


def test_section_nav_indices_after_only_at_the_start():
    # window is [0,1,2] when nothing selected -> nothing before it,
    # index 3 is the one peek item right after
    assert section_nav_indices(count=6, selected_index=None) == (None, 3)


def test_section_nav_indices_both_sides_when_scrolled_into_the_middle():
    # 6 items, selected index 3 -> window [1,2,3] (min(3-3+1, 6-3)=1)
    assert section_nav_indices(count=6, selected_index=3) == (0, 4)


def test_section_nav_indices_before_only_at_the_end():
    # window ends at the last item -> nothing after, one peek before it
    assert section_nav_indices(count=6, selected_index=5) == (2, None)


# ---------- section_rows ----------

def test_section_rows_pads_with_empty_slots_up_to_visible_slots():
    rows = section_rows(items=["a"], error=None, selected_index=None, kind="thing", label="thing")
    assert rows == [
        ("thing", "a"),
        ("empty_slot", "[empty - thing 2]"),
        ("empty_slot", "[empty - thing 3]"),
    ]


def test_section_rows_none_items_and_no_error_is_all_empty_slots():
    rows = section_rows(items=None, error=None, selected_index=None, kind="thing", label="thing")
    assert rows == [
        ("empty_slot", "[empty - thing 1]"),
        ("empty_slot", "[empty - thing 2]"),
        ("empty_slot", "[empty - thing 3]"),
    ]


def test_section_rows_error_takes_the_first_slot_others_stay_empty():
    rows = section_rows(items=None, error="boom", selected_index=None, kind="thing", label="thing")
    assert rows == [
        ("error", "boom"),
        ("empty_slot", "[empty - thing 2]"),
        ("empty_slot", "[empty - thing 3]"),
    ]


def test_section_rows_windows_when_more_items_than_slots():
    items = ["a", "b", "c", "d", "e", "f"]
    rows = section_rows(items=items, error=None, selected_index=4, kind="thing", label="thing")
    assert [payload for _kind, payload in rows] == ["c", "d", "e"]


# ---------- header_with_count ----------

def test_header_with_count_appends_the_real_item_count():
    assert header_with_count("Now Playing", []) == "Now Playing [0]"
    assert header_with_count("Now Playing", ["a", "b"]) == "Now Playing [2]"


def test_header_with_count_omitted_when_unknown():
    # None -> count genuinely unknown (not polled yet, or poll error) -
    # must not claim "[0]", same None-vs-[] discipline as elsewhere.
    assert header_with_count("Output", None) == "Output"
