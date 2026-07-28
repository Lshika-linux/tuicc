"""Tests for the sway provider's tree parsing, using recorded fixtures."""

import json
from pathlib import Path

from i3ipc import Con

from tuicc.providers.sway import parse_tree

FIXTURES = Path(__file__).parent / "fixtures"


def load_state(name):
    with open(FIXTURES / f"{name}.json") as f:
        return parse_tree(Con(json.load(f), None, None))


def test_scratchpad_workspace_is_excluded():
    state = load_state("sway_basic")
    assert [r.id for r in state.regions] == ["2", "3"]


def test_focused_region_is_reported():
    state = load_state("sway_basic")
    assert state.focused_region_id == "2"
    assert [r.id for r in state.regions if r.focused] == ["2"]


def test_floating_window_is_flagged():
    windows = {w.title: w for r in load_state("sway_basic").regions for w in r.windows}
    assert windows["htop"].floating is True
    assert windows["htop"].app_id == "kitty"


def test_rects_are_normalised_against_workspace():
    state = load_state("sway_basic")
    region2 = next(r for r in state.regions if r.id == "2")
    left, right = region2.windows

    assert left.rect[0] == 0.0
    assert right.rect[0] > 0.5
    assert abs(left.rect[2] + right.rect[2] - 1.0) < 0.01
