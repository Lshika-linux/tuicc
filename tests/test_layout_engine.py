"""Tests for layout_engine.py — pure per-box ratio -> absolute cell
math. No coordination between boxes: what a box specifies is exactly
what it gets, regardless of what its neighbors do.
"""

from tuicc.layout import Layout, ModuleBox
from tuicc.layout_engine import compute_boxes


def test_ratio_w_h_scale_with_terminal_size():
    layout = Layout(boxes=[ModuleBox(name="a", x=0.0, y=0.0, w=1.0, h=0.5)])
    assert compute_boxes(layout, 100, 20)["a"] == (0, 0, 100, 10)
    assert compute_boxes(layout, 100, 40)["a"] == (0, 0, 100, 20)


def test_x_y_w_h_all_scale_together():
    layout = Layout(boxes=[ModuleBox(name="a", x=0.25, y=0.5, w=0.5, h=0.25)])
    boxes = compute_boxes(layout, 100, 40)
    assert boxes["a"] == (25, 20, 50, 10)


def test_boxes_do_not_coordinate_with_each_other():
    # Neither box reacts to the other's size or position — each is
    # computed purely from its own x/y/w/h. If two boxes overlap or
    # leave a gap because of how they were configured, that's on the
    # preset author, not the engine.
    layout = Layout(boxes=[
        ModuleBox(name="top", x=0.0, y=0.0, w=1.0, h=0.6),
        ModuleBox(name="bottom", x=0.0, y=0.6, w=1.0, h=0.1),
    ])
    boxes_short = compute_boxes(layout, 100, 20)
    boxes_tall = compute_boxes(layout, 100, 100)
    assert boxes_short["top"][3] == 12
    assert boxes_tall["top"][3] == 60
    assert boxes_short["bottom"][1] == 12  # round(0.6 * 20)
    assert boxes_tall["bottom"][1] == 60


def test_multiple_boxes_computed_independently():
    layout = Layout(boxes=[
        ModuleBox(name="left", x=0.0, y=0.0, w=0.5, h=1.0),
        ModuleBox(name="right", x=0.5, y=0.0, w=0.5, h=1.0),
    ])
    boxes = compute_boxes(layout, 100, 40)
    assert boxes["left"] == (0, 0, 50, 40)
    assert boxes["right"] == (50, 0, 50, 40)


def test_real_preset_1_loads_and_every_box_is_within_bounds(tmp_path, monkeypatch):
    import tuicc.config as config_module
    from tuicc.config import build_layout_from_preset

    # Point USER_PRESETS_DIR at an empty tmp dir so this genuinely
    # exercises the packaged preset shipped in the repo — without this,
    # build_layout_from_preset(1) prefers ~/.config/tuicc/presets/1.toml
    # when one happens to exist there (see ensure_preset_exists' own
    # docstring), so this test would silently pass or fail based on
    # whatever preset a given dev machine has saved locally instead of
    # what's actually packaged.
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user-presets")

    layout = build_layout_from_preset(1)
    boxes = compute_boxes(layout, term_width=100, term_height=40)
    assert set(boxes.keys()) == {
        "sidebar", "connectivity", "power_menu", "launcher", "sessions", "preview", "clock",
        "control", "media", "bars", "sysmon",
    }
    for x, y, w, h in boxes.values():
        assert 0 <= x <= 100
        assert 0 <= y <= 40
        assert w > 0
        assert h > 0
