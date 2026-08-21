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


def test_fh_stays_fixed_regardless_of_terminal_height():
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=10)])

    assert compute_boxes(layout, 100, 20)["control"][3] == 10
    assert compute_boxes(layout, 100, 200)["control"][3] == 10


def test_fh_does_not_affect_width_which_stays_ratio():
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=10)])

    boxes = compute_boxes(layout, 100, 40)

    assert boxes["control"][2] == 20  # round(0.2 * 100), unaffected by fh


# ---------- fh: holding the bottom edge instead of clipping ----------

def test_fh_holds_its_own_bottom_edge_when_ratio_y_would_overflow():
    # y=0.9 * 20 = 18, fh=10 -> 18+10=28, past the 20-row terminal.
    # Must NOT shrink (still exactly 10 rows) — slides y up instead so
    # y+h lands exactly at the bottom edge (20).
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.9, w=0.2, h=None, fh=10)])

    x, y, w, h = compute_boxes(layout, 100, 20)["control"]

    assert h == 10  # never shrinks
    assert y == 10  # 20 - 10, flush against the bottom
    assert y + h == 20  # fully on screen, nothing clipped


def test_fh_leaves_y_alone_when_it_already_fits():
    # y=0.5 * 40 = 20, fh=10 -> 20+10=30, well within the 40-row
    # terminal — the edge-hold logic must not kick in here at all.
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=10)])

    x, y, w, h = compute_boxes(layout, 100, 40)["control"]

    assert y == 20  # untouched, plain round(0.5 * 40)
    assert h == 10


def test_fh_flush_against_the_bottom_is_left_alone():
    # y already computes to exactly term_height - fh — the "already
    # flush" case must not be perturbed by the clamp.
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.75, w=0.2, h=None, fh=10)])

    x, y, w, h = compute_boxes(layout, 100, 40)["control"]

    assert y == 30  # round(0.75 * 40)
    assert y + h == 40


def test_fh_larger_than_the_whole_terminal_starts_flush_at_the_top():
    # Pathological case: fh (15) itself exceeds term_height (10) — still
    # can't fit, but starts at row 0 rather than a negative row.
    layout = Layout(boxes=[ModuleBox(name="control", x=0.0, y=0.5, w=0.2, h=None, fh=15)])

    x, y, w, h = compute_boxes(layout, 100, 10)["control"]

    assert y == 0
    assert h == 15


def test_ratio_h_boxes_are_never_edge_held_just_scaled():
    # A plain ratio box overflowing the bottom isn't this mechanism's
    # job — h itself already scales down with the terminal, it can't
    # "overflow" the way a fixed fh can.
    layout = Layout(boxes=[ModuleBox(name="sidebar", x=0.0, y=0.9, w=0.2, h=0.5)])

    x, y, w, h = compute_boxes(layout, 100, 20)["sidebar"]

    assert y == 18  # round(0.9 * 20), untouched
    assert h == 10  # round(0.5 * 20) — still overflows past the bottom, and that's accepted/expected for ratio boxes


# ---------- fh vs fh: chaining stacked fixed-row boxes ----------

def test_fh_box_above_another_fh_box_chains_flush_instead_of_overlapping():
    # Their own natural (ratio) positions already overlap on this
    # terminal — "above" must chain to sit flush against "below"
    # instead of drawing straight into it.
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.3, w=1.0, h=None, fh=6),
        ModuleBox(name="below", x=0.0, y=0.6, w=1.0, h=None, fh=5),
    ])
    boxes = compute_boxes(layout, 100, 12)

    assert boxes["below"] == (0, 7, 100, 5)  # "below" is untouched — nothing sits below IT
    assert boxes["above"][1] + boxes["above"][3] == boxes["below"][1]  # "above" sits flush on top
    assert boxes["above"][3] == 6  # never shrinks


def test_fh_box_untouched_when_it_already_fits_above_its_neighbor():
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=1.0, h=None, fh=3),
        ModuleBox(name="below", x=0.0, y=0.5, w=1.0, h=None, fh=5),
    ])
    boxes = compute_boxes(layout, 100, 20)

    assert boxes["above"] == (0, 0, 100, 3)  # plenty of room, no chaining needed
    assert boxes["below"] == (0, 10, 100, 5)


def test_fh_chain_ignores_a_box_with_no_horizontal_overlap():
    layout = Layout(boxes=[
        ModuleBox(name="side", x=0.5, y=0.5, w=0.5, h=None, fh=10),
        ModuleBox(name="stack_top", x=0.0, y=0.3, w=0.5, h=None, fh=6),
        ModuleBox(name="stack_bottom", x=0.0, y=0.6, w=0.5, h=None, fh=5),
    ])
    boxes = compute_boxes(layout, 100, 12)

    # "side" is in a different column entirely — must not affect the
    # left-column stack's own chaining at all.
    assert boxes["stack_bottom"] == (0, 7, 50, 5)
    assert boxes["stack_top"][1] + boxes["stack_top"][3] == boxes["stack_bottom"][1]


def test_fh_chain_of_three_stacked_boxes_cascades_correctly():
    layout = Layout(boxes=[
        ModuleBox(name="top", x=0.0, y=0.1, w=1.0, h=None, fh=4),
        ModuleBox(name="middle", x=0.0, y=0.4, w=1.0, h=None, fh=4),
        ModuleBox(name="bottom", x=0.0, y=0.7, w=1.0, h=None, fh=4),
    ])
    boxes = compute_boxes(layout, 100, 12)  # exactly 3*4=12 — fits perfectly, snug

    assert boxes["bottom"] == (0, 8, 100, 4)
    assert boxes["middle"] == (0, 4, 100, 4)
    assert boxes["top"] == (0, 0, 100, 4)


def test_fh_chain_degrades_honestly_when_the_whole_stack_cannot_fit():
    # 6+5=11 rows needed, only 8 available — genuinely impossible.
    # "above" clamps at y=0 (can't go higher) and the two DO still
    # overlap — accepted, documented degradation, not silently hidden.
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.3, w=1.0, h=None, fh=6),
        ModuleBox(name="below", x=0.0, y=0.6, w=1.0, h=None, fh=5),
    ])
    boxes = compute_boxes(layout, 100, 8)

    assert boxes["above"] == (0, 0, 100, 6)  # never shrinks, never goes negative
    assert boxes["below"][3] == 5  # never shrinks either
    # they DO overlap here — there simply isn't a non-overlapping answer
    assert boxes["above"][1] + boxes["above"][3] > boxes["below"][1]


def test_fh_chain_uses_the_1_cell_tolerance_same_as_flexible_squeeze():
    # Two fh boxes with only a 1-cell horizontal overlap (rounding-scale)
    # must not chain against each other — same tolerance reasoning as
    # the flexible-squeeze pass above.
    above = ModuleBox(name="above", x=0.0, y=0.3, w=0.51, h=None, fh=6)
    below = ModuleBox(name="below", x=0.5, y=0.6, w=0.5, h=None, fh=5)
    layout = Layout(boxes=[above, below])
    boxes = compute_boxes(layout, 100, 12)

    # above: x=0,w=51 (0-51); below: x=50,w=50 (50-100) -> exactly 1 cell overlap
    assert min(boxes["above"][0] + boxes["above"][2], boxes["below"][0] + boxes["below"][2]) - max(boxes["above"][0], boxes["below"][0]) == 1
    assert boxes["above"][1] == 4  # untouched (round(0.3*12)) — 1-cell overlap is tolerance, not a real collision


# ---------- flexible boxes shrinking to avoid intruding on neighbors ----------

def test_flexible_box_shrinks_from_top_when_a_box_above_intrudes():
    # "above" (fh, edge-holding not relevant here) ends at y=5; "below"
    # (flexible, h ratio) naturally starts at y=2 — 3 rows of overlap.
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=1.0, h=None, fh=5),
        ModuleBox(name="below", x=0.0, y=0.2, w=1.0, h=0.8),
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["below"]

    assert y == 5  # pushed down to "above"'s bottom edge
    assert y + h == 10  # bottom edge untouched — only the top yielded


def test_flexible_box_shrinks_from_bottom_when_a_box_below_intrudes():
    layout = Layout(boxes=[
        ModuleBox(name="top", x=0.0, y=0.0, w=1.0, h=0.8),
        ModuleBox(name="below", x=0.0, y=0.5, w=1.0, h=None, fh=5),
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["top"]

    assert y == 0  # top edge untouched
    assert y + h == 5  # cut off right where "below" starts


def test_flexible_box_sandwiched_between_two_neighbors_shrinks_both_sides():
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=1.0, h=None, fh=3),
        ModuleBox(name="middle", x=0.0, y=0.1, w=1.0, h=0.8),
        ModuleBox(name="below", x=0.0, y=0.6, w=1.0, h=None, fh=4),
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["middle"]

    assert y == 3       # "above"'s bottom edge (fh=3)
    assert y + h == 6    # "below" starts at y=6


def test_flexible_box_squeezed_out_entirely_gets_zero_height():
    # "above" and "below" together leave no room at all for "middle".
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=1.0, h=None, fh=6),
        ModuleBox(name="middle", x=0.0, y=0.1, w=1.0, h=0.8),
        ModuleBox(name="below", x=0.0, y=0.5, w=1.0, h=None, fh=5),
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["middle"]

    assert h == 0  # never negative, never overlapping either neighbor


def test_flexible_box_untouched_when_it_does_not_actually_overlap_anything():
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=1.0, h=None, fh=2),
        ModuleBox(name="middle", x=0.0, y=0.3, w=1.0, h=0.4),  # y=3, h=4 -> 3..7, clear of both neighbors
        ModuleBox(name="below", x=0.0, y=0.8, w=1.0, h=None, fh=2),  # y=8
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["middle"]

    assert (y, h) == (3, 4)  # exactly its own natural rect, no shrink applied


def test_flexible_box_ignores_a_box_with_no_horizontal_overlap():
    # "side" sits in a completely different column — real y-overlap
    # with "flex" but zero x-overlap, so it must not affect it at all.
    layout = Layout(boxes=[
        ModuleBox(name="side", x=0.5, y=0.0, w=0.5, h=None, fh=10),
        ModuleBox(name="flex", x=0.0, y=0.0, w=0.5, h=1.0),
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["flex"]

    assert (y, h) == (0, 10)  # untouched — "side" is in a different column


def test_flexible_boxes_do_not_shrink_each_other_when_stacked_cleanly():
    # Two flexible boxes, no overlap by construction — confirms the
    # mechanism doesn't introduce spurious shrinkage between two ratio
    # boxes that were already configured not to collide.
    layout = Layout(boxes=[
        ModuleBox(name="top", x=0.0, y=0.0, w=1.0, h=0.5),
        ModuleBox(name="bottom", x=0.0, y=0.5, w=1.0, h=0.5),
    ])
    boxes = compute_boxes(layout, 100, 20)

    assert boxes["top"] == (0, 0, 100, 10)
    assert boxes["bottom"] == (0, 10, 100, 10)


def test_a_1_cell_horizontal_overlap_from_independent_rounding_does_not_trigger_a_collapse():
    # Live-reproduced bug: two flexible, side-by-side columns whose
    # ratio edges are a hair's width apart (independently hand-tuned,
    # not perfectly aligned) can round to overlapping by exactly 1 cell
    # at some terminal widths — with equal y, that used to collapse
    # BOTH to 0 height. A 1-cell overlap must now be treated as
    # rounding noise, same as no overlap at all, at every width where
    # it occurs.
    left = ModuleBox(name="left", x=0.1875, y=0.078431, w=0.734375, h=0.705882)
    right = ModuleBox(name="right", x=0.920833, y=0.086275, w=0.078125, h=0.709804)
    layout = Layout(boxes=[left, right])

    saw_a_1_cell_overlap = False
    for term_w in range(30, 200):
        boxes = compute_boxes(layout, term_w, term_height=51)
        lx, ly, lw, lh = boxes["left"]
        rx, ry, rw, rh = boxes["right"]
        overlap = min(lx + lw, rx + rw) - max(lx, rx)
        if overlap == 1:
            saw_a_1_cell_overlap = True
            assert lh > 0 and rh > 0, (
                f"w={term_w}: a 1-cell rounding overlap collapsed a box "
                f"(left h={lh}, right h={rh})"
            )
    assert saw_a_1_cell_overlap, "test setup didn't actually hit the rounding case it's meant to guard"


def test_a_genuine_2_cell_horizontal_overlap_still_triggers_a_squeeze():
    # The tolerance must not swallow REAL overlaps — only ones at or
    # below the 1-cell rounding-noise threshold.
    layout = Layout(boxes=[
        ModuleBox(name="above", x=0.0, y=0.0, w=0.5, h=None, fh=5),
        ModuleBox(name="below", x=0.0, y=0.2, w=0.5, h=0.8),  # y=2, shares 50 of 100 columns with "above"
    ])
    x, y, w, h = compute_boxes(layout, 100, 10)["below"]

    assert y == 5
    assert h == 5


def test_fh_boxes_never_shrink_even_if_a_flexible_neighbor_would_intrude():
    # The shrink mechanism only ever touches flexible (h) boxes — an fh
    # box's own edge-hold behavior (already tested above) must stay
    # completely unaffected by this second pass.
    layout = Layout(boxes=[
        ModuleBox(name="fixed", x=0.0, y=0.5, w=1.0, h=None, fh=10),
        ModuleBox(name="flex", x=0.0, y=0.0, w=1.0, h=1.0),
    ])
    boxes = compute_boxes(layout, 100, 10)

    assert boxes["fixed"][3] == 10  # unchanged fh, even though flex "wants" that space too
    assert boxes["flex"][3] == 0    # flex is the one that yields


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
        "sidebar", "connectivity", "power_menu", "launcher", "sessions", "preview", "rwb",
        "control", "media", "bars", "sysmon",
    }
    for x, y, w, h in boxes.values():
        assert 0 <= x <= 100
        assert 0 <= y <= 40
        assert w > 0
        assert h > 0
