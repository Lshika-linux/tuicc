"""Tests for the sway provider's tree parsing, using recorded fixtures."""

import json
from pathlib import Path

from i3ipc import Con

from tuicc.providers.sway import parse_tree, MARK_PREFIX, _stale_self_marks

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


def _leaf(id_, marks=(), floating_type=False, pid=None):
    return {
        "id": id_, "type": "floating_con" if floating_type else "con",
        "app_id": "kitty", "name": f"window-{id_}",
        "focused": False, "marks": list(marks), "pid": pid,
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


# ---------- _stale_self_marks ----------

def test_stale_self_marks_flags_a_mark_on_the_wrong_window():
    # The actual live bug: a mark whose embedded pid (111) belongs to
    # some OTHER, long-gone tuicc process — but the window it's
    # actually sitting on has a completely different real pid (9219,
    # an unrelated app).
    tree = _tree_with_windows(tiled=[_leaf(50, marks=[f"{MARK_PREFIX}111"], pid=9219)])
    assert _stale_self_marks(tree) == [("50", f"{MARK_PREFIX}111")]


def test_stale_self_marks_leaves_a_correctly_matched_mark_alone():
    # A legitimate self-mark: the embedded pid IS this window's own
    # real pid — exactly what mark_self() produces under normal
    # operation.
    tree = _tree_with_windows(tiled=[_leaf(51, marks=[f"{MARK_PREFIX}111"], pid=111)])
    assert _stale_self_marks(tree) == []


def test_stale_self_marks_ignores_marks_that_are_not_tuiccs_own():
    tree = _tree_with_windows(tiled=[_leaf(52, marks=["some-other-mark"], pid=9219)])
    assert _stale_self_marks(tree) == []


def test_stale_self_marks_ignores_a_malformed_pid_suffix():
    # Shouldn't happen in practice (tuicc always suffixes with a real
    # int pid), but a non-numeric suffix must not crash this — just
    # isn't ours to touch.
    tree = _tree_with_windows(tiled=[_leaf(53, marks=[f"{MARK_PREFIX}not-a-pid"], pid=9219)])
    assert _stale_self_marks(tree) == []


def test_stale_self_marks_checks_floating_windows_too():
    tree = _tree_with_windows(floating=[
        _leaf(54, marks=[f"{MARK_PREFIX}111"], pid=9219, floating_type=True),
    ])
    assert _stale_self_marks(tree) == [("54", f"{MARK_PREFIX}111")]


def test_stale_self_marks_finds_multiple_across_the_tree():
    tree = _tree_with_windows(tiled=[
        _leaf(55, marks=[f"{MARK_PREFIX}111"], pid=9219),  # stale
        _leaf(56, marks=[f"{MARK_PREFIX}222"], pid=222),   # legitimate
        _leaf(57, marks=[f"{MARK_PREFIX}333"], pid=9220),  # stale
    ])
    assert _stale_self_marks(tree) == [("55", f"{MARK_PREFIX}111"), ("57", f"{MARK_PREFIX}333")]
