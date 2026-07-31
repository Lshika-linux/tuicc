"""Tests for the sway provider's tree parsing, using recorded fixtures."""

import json
from pathlib import Path

from i3ipc import Con

from tuicc.providers.sway import parse_tree, MARK_PREFIX

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


def _tree_with_windows(tiled=(), floating=()):
    """A minimal synthetic tree — just enough fields for parse_tree() to
    work — rather than a full recorded fixture, since these tests only
    care about the marks-based filtering, not realistic geometry.
    """
    return Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": 2, "type": "workspace", "num": 1, "name": "1",
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": list(floating),
            "nodes": list(tiled),
        }],
    }, None, None)


def _leaf(id_, marks=(), floating_type=False):
    return {
        "id": id_, "type": "floating_con" if floating_type else "con",
        "app_id": "kitty", "name": f"window-{id_}",
        "focused": False, "marks": list(marks),
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
    }


def test_marked_tiled_window_is_excluded_from_state():
    tree = _tree_with_windows(tiled=[_leaf(10), _leaf(11, marks=[f"{MARK_PREFIX}12345"])])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["10"]


def test_marked_floating_window_is_excluded_from_state():
    # floating_type=True — matches real sway data, where a floating leaf's
    # own type is "floating_con" (carries the window's properties
    # directly), not wrapped like i3's floating_con. Getting this wrong
    # makes leaves() double-count the floating leaf too (its type would
    # match "con" and get picked up a second time via workspace.leaves()'s
    # internal walk of floating_nodes) — a real trap this test guards against.
    tree = _tree_with_windows(floating=[
        _leaf(20, floating_type=True),
        _leaf(21, marks=[f"{MARK_PREFIX}12345"], floating_type=True),
    ])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["20"]


def test_unmarked_windows_are_unaffected():
    # An ordinary mark the user set themselves (not tuicc's) must not be
    # treated as "self" — only marks starting with MARK_PREFIX are filtered.
    tree = _tree_with_windows(tiled=[_leaf(30, marks=["some-other-mark"])])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["30"]


def test_two_different_tuicc_instances_both_get_excluded():
    # The actual scenario the PID suffix exists for: two tuicc windows,
    # each carrying its OWN process's mark (different PIDs, since marks
    # must be globally unique — see the module docstring). Every tuicc
    # instance's parse_tree() should exclude every tuicc window, not just
    # the one matching its own PID.
    tree = _tree_with_windows(tiled=[
        _leaf(40),
        _leaf(41, marks=[f"{MARK_PREFIX}111"]),
        _leaf(42, marks=[f"{MARK_PREFIX}222"]),
    ])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["40"]
