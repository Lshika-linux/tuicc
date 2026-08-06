"""Tests for sidebar.py — nav_items and the pure height/preview helpers
(_slot_height, _preview_apps_for). draw() needs a real curses screen to
exercise meaningfully, same as sidebar_compact.py and other modules'
drawing code is left untested here.
"""

from types import SimpleNamespace

from tuicc.model import Region, Window, WMState
from tuicc.modules.sidebar import nav_items, _slot_height, _preview_apps_for


def _ctx(regions, total_workspaces=3, selected_id=None, session_preview=None):
    return SimpleNamespace(
        state=WMState(regions=regions),
        config=SimpleNamespace(total_workspaces=total_workspaces),
        selected_id=selected_id,
        session_preview=session_preview,
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
