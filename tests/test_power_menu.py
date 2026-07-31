"""Tests for power_menu.py — only the pure logic (_row_label, nav_items).
draw() needs a real curses screen to exercise meaningfully, so it's left
untested here, same as other modules' drawing code.
"""

from types import SimpleNamespace

from tuicc.modules.power_menu import _row_label, nav_items


def test_row_label_with_shortcut():
    action = {"label": "Lock", "shortcut": "Ctrl+L"}
    assert _row_label(action) == "[^L] Lock"


def test_row_label_without_shortcut():
    action = {"label": "Lock", "shortcut": None}
    assert _row_label(action) == "Lock"


def test_row_label_missing_shortcut_key_defaults_to_label_only():
    # .get("shortcut") — a dict that never had the key at all behaves
    # the same as one with shortcut=None.
    action = {"label": "Lock"}
    assert _row_label(action) == "Lock"


def _fake_ctx(actions):
    return SimpleNamespace(config=SimpleNamespace(power_menu_actions=actions))


def test_nav_items_one_row_per_action():
    actions = [
        {"label": "Lock", "command": "swaylock"},
        {"label": "Logout", "command": "swaymsg exit"},
    ]
    ctx = _fake_ctx(actions)
    box = (0, 0, 20, 10)  # x, y, w, h — plenty of room

    items = nav_items(box, ctx, "power_menu")

    assert len(items) == 2
    assert items[0].id == "power_menu:0"
    assert items[0].rect[1] == 1  # y + 1 (first row inside the border)
    assert items[1].id == "power_menu:1"
    assert items[1].rect[1] == 2  # next row down


def test_nav_items_stops_at_box_bottom_border():
    # A box only tall enough for 2 action rows (h=4: border, row, row, border)
    # should not report a 3rd action as a nav item even if configured.
    actions = [
        {"label": "A", "command": "true"},
        {"label": "B", "command": "true"},
        {"label": "C", "command": "true"},
    ]
    ctx = _fake_ctx(actions)
    box = (0, 0, 20, 4)

    items = nav_items(box, ctx, "power_menu")

    assert len(items) == 2


def test_nav_items_target_kind_is_power_action():
    ctx = _fake_ctx([{"label": "Lock", "command": "swaylock"}])
    items = nav_items((0, 0, 20, 10), ctx, "power_menu")
    assert items[0].target_kind == "power_action"
    assert items[0].focus_target == "swaylock"


def test_nav_items_handles_more_than_four_actions():
    # power_menu is no longer hardcoded to 4 actions (removed the
    # grid's [:4] slice) — a 5th configured action should get a nav item
    # as long as the box is tall enough.
    actions = [{"label": f"A{i}", "command": "true"} for i in range(5)]
    ctx = _fake_ctx(actions)
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "power_menu")

    assert len(items) == 5
    assert items[4].id == "power_menu:4"
