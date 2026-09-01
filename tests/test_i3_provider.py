"""Tests for the i3 provider's tree parsing, using recorded fixtures."""

import json
import os
from pathlib import Path

from i3ipc import Con

import tuicc.providers.i3 as i3_module
from tuicc.providers.i3 import parse_tree, MARK_PREFIX, _stale_self_marks, _self_focused

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


def _tiled_leaf(id_, marks=(), focused=False):
    return {
        "id": id_, "type": "con", "app_id": None, "window_class": "XTerm",
        "name": f"window-{id_}", "focused": focused, "marks": list(marks),
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
    }


def _floating_leaf(id_, marks=(), focused=False):
    # i3 wraps every floating window in a floating_con container that
    # carries none of the window's own properties — the real window (with
    # its marks) is the single nested child, same shape _unwrap_floating()
    # expects in the real provider code.
    return {
        "id": id_ * 100, "type": "floating_con", "marks": [],
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
        "nodes": [{
            "id": id_, "type": "con", "app_id": None, "window_class": "XTerm",
            "name": f"window-{id_}", "focused": focused, "marks": list(marks),
            "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
        }],
    }


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


def test_marked_tiled_window_is_excluded_from_state():
    tree = _tree_with_windows(tiled=[_tiled_leaf(10), _tiled_leaf(11, marks=[f"{MARK_PREFIX}12345"])])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["10"]


def test_marked_floating_window_is_excluded_from_state():
    tree = _tree_with_windows(floating=[
        _floating_leaf(20),
        _floating_leaf(21, marks=[f"{MARK_PREFIX}12345"]),
    ])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["20"]


def test_unmarked_windows_are_unaffected():
    tree = _tree_with_windows(tiled=[_tiled_leaf(30, marks=["some-other-mark"])])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["30"]


def test_two_different_tuicc_instances_both_get_excluded():
    tree = _tree_with_windows(tiled=[
        _tiled_leaf(40),
        _tiled_leaf(41, marks=[f"{MARK_PREFIX}111"]),
        _tiled_leaf(42, marks=[f"{MARK_PREFIX}222"]),
    ])
    state = parse_tree(tree)
    windows = [w for r in state.regions for w in r.windows]
    assert [w.id for w in windows] == ["40"]


# ---------- _stale_self_marks ----------
#
# i3's own GET_TREE has no pid field (see _leaf_to_window's own comment
# on this) — _stale_self_marks falls back to _x11_pid_for_window()'s
# on-demand X11 lookup, monkeypatched here so these tests don't need a
# real X server. window=<some int> stands in for the X11 window id
# _x11_pid_for_window() would be called with.

def _leaf_with_window(id_, marks=(), window=None):
    return {
        "id": id_, "type": "con", "app_id": None, "window_class": "XTerm",
        "name": f"window-{id_}", "focused": False, "marks": list(marks),
        "window": window,
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
    }


def test_stale_self_marks_flags_a_mark_on_the_wrong_window(monkeypatch):
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: 9219)
    tree = _tree_with_windows(tiled=[_leaf_with_window(50, marks=[f"{MARK_PREFIX}111"], window=555)])
    assert _stale_self_marks(tree) == [("50", f"{MARK_PREFIX}111")]


def test_stale_self_marks_leaves_a_correctly_matched_mark_alone(monkeypatch):
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: 111)
    tree = _tree_with_windows(tiled=[_leaf_with_window(51, marks=[f"{MARK_PREFIX}111"], window=555)])
    assert _stale_self_marks(tree) == []


def test_stale_self_marks_treats_a_failed_x11_lookup_as_inconclusive_not_stale(monkeypatch):
    # A real, unavoidable gap on i3 (no DISPLAY, window already gone, a
    # client that never set the EWMH hint) — must not wrongly strip a
    # possibly-legitimate mark just because the lookup itself failed.
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: None)
    tree = _tree_with_windows(tiled=[_leaf_with_window(52, marks=[f"{MARK_PREFIX}111"], window=555)])
    assert _stale_self_marks(tree) == []


def test_stale_self_marks_skips_a_window_with_no_x11_window_id(monkeypatch):
    calls = []
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: calls.append(xid) or 9219)
    tree = _tree_with_windows(tiled=[_leaf_with_window(53, marks=[f"{MARK_PREFIX}111"], window=None)])
    assert _stale_self_marks(tree) == []
    assert calls == []  # never even attempted the lookup


def test_stale_self_marks_ignores_marks_that_are_not_tuiccs_own(monkeypatch):
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: 9219)
    tree = _tree_with_windows(tiled=[_leaf_with_window(54, marks=["some-other-mark"], window=555)])
    assert _stale_self_marks(tree) == []


# ---------- _self_focused ----------
# os.getpid() (the real running test process's own pid) is what
# mark_self() would actually embed in a real run — using it here, not
# an arbitrary fake pid, is what makes these tests exercise the exact
# mark _self_focused() looks for. No X11 mocking needed — unlike
# _stale_self_marks(), this doesn't touch pid lookups at all, just
# marks/focused straight off the tree.

def test_self_focused_true_when_the_marked_window_is_focused():
    pid = os.getpid()
    tree = _tree_with_windows(tiled=[_tiled_leaf(60, marks=[f"{MARK_PREFIX}{pid}"], focused=True)])
    assert _self_focused(tree) is True


def test_self_focused_false_when_the_marked_window_is_not_focused():
    pid = os.getpid()
    tree = _tree_with_windows(tiled=[
        _tiled_leaf(61, marks=[f"{MARK_PREFIX}{pid}"], focused=False),
        _tiled_leaf(62, focused=True),
    ])
    assert _self_focused(tree) is False


def test_self_focused_checks_floating_windows_too():
    pid = os.getpid()
    tree = _tree_with_windows(floating=[_floating_leaf(63, marks=[f"{MARK_PREFIX}{pid}"], focused=True)])
    assert _self_focused(tree) is True


def test_self_focused_none_when_the_mark_is_not_in_the_tree_at_all():
    tree = _tree_with_windows(tiled=[_tiled_leaf(64)])
    assert _self_focused(tree) is None


def test_self_focused_ignores_a_different_tuicc_instances_own_mark():
    tree = _tree_with_windows(tiled=[_tiled_leaf(65, marks=[f"{MARK_PREFIX}999999999"], focused=True)])
    assert _self_focused(tree) is None
