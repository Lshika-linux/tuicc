"""Tests for sidebar_compact.py — only nav_items (pure logic). draw()
needs a real curses screen to exercise meaningfully, same as sidebar.py
and other modules' drawing code is left untested here.
"""

from types import SimpleNamespace

from tuicc.model import Region, Window, WMState
from tuicc.modules.sidebar_compact import nav_items


def _ctx(regions, total_workspaces=3, selected_id=None, wm_config=None):
    return SimpleNamespace(
        state=WMState(regions=regions),
        config=SimpleNamespace(total_workspaces=total_workspaces),
        selected_id=selected_id,
        wm_config=wm_config,
    )


def test_nav_items_one_per_workspace_slot():
    ctx = _ctx(regions=[], total_workspaces=3)

    items = nav_items((0, 0, 6, 10), ctx, "sidebar_compact")

    assert [item.focus_target for item in items] == ["1", "2", "3"]


def test_nav_items_are_region_kind_by_default():
    ctx = _ctx(regions=[], total_workspaces=1)

    items = nav_items((0, 0, 6, 10), ctx, "sidebar_compact")

    # No explicit target_kind set — same "region" default sidebar.py's
    # items get, so selecting one flows through the exact same
    # resolve_selection machinery.
    assert items[0].target_kind == "region"


def test_nav_items_use_module_prefixed_ids():
    ctx = _ctx(regions=[], total_workspaces=1)

    items = nav_items((0, 0, 6, 10), ctx, "sidebar_compact")

    assert items[0].id == "sidebar_compact:1"


def test_nav_items_one_row_each_regardless_of_window_count():
    # Unlike sidebar.py, a slot is always exactly 1 row tall — no
    # per-window growth, that's the whole point of "compact". Rows are
    # spaced 2 apart (a blank spacer row between each), not adjacent.
    busy_region = Region(id="1", name="1", windows=[
        Window(id="w1", app_id="a", title="", focused=False, rect=(0, 0, 1, 1)),
        Window(id="w2", app_id="b", title="", focused=False, rect=(0, 0, 1, 1)),
    ])
    ctx = _ctx(regions=[busy_region], total_workspaces=2)

    items = nav_items((0, 0, 6, 10), ctx, "sidebar_compact")

    assert [item.rect[3] for item in items] == [1, 1]
    assert items[1].rect[1] == items[0].rect[1] + 2


def test_nav_items_stop_at_box_bottom_edge():
    # Box only has room for 1 spaced-out slot (h=4: border + 2 rows +
    # border, but slots are 2 rows apart) even though total_workspaces
    # asks for 5 — no items overflow past the box's own bottom border.
    ctx = _ctx(regions=[], total_workspaces=5)

    items = nav_items((0, 0, 6, 4), ctx, "sidebar_compact")

    assert len(items) == 1