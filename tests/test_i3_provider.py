"""Tests for the i3 provider's tree parsing, using recorded fixtures."""

import json
from pathlib import Path

from i3ipc import Con

from tuicc.providers.i3 import parse_tree

FIXTURES = Path(__file__).parent / "fixtures"


def load_state(name):
    with open(FIXTURES / f"{name}.json") as f:
        return parse_tree(Con(json.load(f), None, None))


def test_scratchpad_workspace_is_excluded():
    state = load_state("i3_basic")
    assert [r.id for r in state.regions] == ["1", "3"]


def test_floating_windows_are_unwrapped():
    state = load_state("i3_basic")
    for region in state.regions:
        for w in region.windows:
            assert w.app_id == "XTerm"
            assert w.id != ""


def test_window_class_used_when_no_app_id():
    state = load_state("i3_basic")
    all_windows = [w for r in state.regions for w in r.windows]
    assert all(w.app_id == "XTerm" for w in all_windows)

def test_no_duplicate_windows_between_leaves_and_floating():
    state = load_state("i3_tiled")
    for region in state.regions:
        ids = [w.id for w in region.windows]
        assert len(ids) == len(set(ids)), f"duplicate window id in region {region.id}: {ids}"


def test_floating_flag_matches_actual_floating_state():
    state = load_state("i3_tiled")
    windows = {w.id: w for r in state.regions for w in r.windows}
    # workspace 1 has two floating xterms and (currently) some leftover
    # tiled ones from earlier manual testing; workspace 3 has one of each.
    floating_count = sum(1 for w in windows.values() if w.floating)
    assert floating_count == 3  # 2 on ws1 + 1 on ws3, per the i3_tiled fixture

def test_full_scene_matches_expected_layout():
    state = load_state("i3_full_scene")
    by_id = {r.id: r for r in state.regions}

    # workspace 1: two tiled windows, side by side, no overlap
    ws1 = by_id["1"]
    assert len(ws1.windows) == 2
    assert all(w.floating is False for w in ws1.windows)
    left, right = sorted(ws1.windows, key=lambda w: w.rect[0])
    assert left.rect[0] == 0.0
    assert right.rect[0] == 0.5

    # workspace 2: single tiled window filling the workspace
    ws2 = by_id["2"]
    assert len(ws2.windows) == 1
    assert ws2.windows[0].floating is False
    assert ws2.windows[0].rect == (0.0, 0.0, 1.0, 1.0)

    # workspace 3: two tiled + two floating, floating windows overlap
    ws3 = by_id["3"]
    tiled = [w for w in ws3.windows if not w.floating]
    floating = [w for w in ws3.windows if w.floating]
    assert len(tiled) == 2
    assert len(floating) == 2

    def overlaps(a, b):
        ax, ay, aw, ah = a.rect
        bx, by, bw, bh = b.rect
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    assert overlaps(floating[0], floating[1])
