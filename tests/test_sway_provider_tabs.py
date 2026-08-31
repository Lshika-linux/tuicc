"""Tests for stacked/tabbed container handling (GitHub issue #8) —
Window.tab_group_id/tab_group_layout/tab_active, populated by
tab_groups.tab_info_by_leaf_id() and providers/sway.py's parse_tree().

Fixtures below mirror two real trees confirmed live against the actual
machine this feature was built against (2026-08-31): a simple 2-window
stacked case (VS Code + Firefox) and a nested case (an outer stacked
container with a splitv branch that itself contains an inner stacked
container one level down) — see chat history for the real
swaymsg -t get_tree dumps these are modeled on.
"""

from i3ipc import Con

from tuicc.providers.sway import parse_tree
from tuicc.tab_groups import tab_info_by_leaf_id


def _leaf(id_, app_id="kitty", name="w"):
    return {
        "id": id_, "type": "con", "app_id": app_id, "name": name,
        "focused": False, "marks": [], "pid": 1,
        "rect": {"x": 6, "y": 66, "width": 953, "height": 961},
        "nodes": [], "floating_nodes": [],
    }


def _con(id_, layout, focus, nodes):
    return {
        "id": id_, "type": "con", "app_id": None, "name": None,
        "layout": layout, "focus": focus,
        "rect": {"x": 6, "y": 6, "width": 1908, "height": 1041},
        "nodes": nodes, "floating_nodes": [],
    }


def _workspace(id_, name, nodes, focus=None):
    return Con({
        "id": id_, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "nodes": [{
            "id": id_ + 1, "type": "workspace", "num": int(name), "name": name,
            "rect": {"x": 6, "y": 6, "width": 1908, "height": 1041},
            "focus": focus or [],
            "nodes": nodes, "floating_nodes": [],
        }],
    }, None, None)


# ---------- tab_info_by_leaf_id ----------

def test_simple_stacked_pair_active_child_marked():
    # Mirrors the real ws10 case: two leaves directly under one
    # "stacked" container, wrapped in the splith cons sway always
    # inserts (confirmed live — a stacked/tabbed container's own
    # .nodes are these wrapper cons, not the leaves directly).
    code = _leaf(9, app_id="code")
    firefox = _leaf(148, app_id="firefox")
    wrapper_code = _con(149, "splith", [9], [code])
    wrapper_firefox = _con(150, "splith", [148], [firefox])
    stacked = _con(144, "stacked", [149, 150], [wrapper_code, wrapper_firefox])
    tree = _workspace(8, "10", [stacked], focus=[144])

    info = tab_info_by_leaf_id(tree.workspaces()[0])

    assert info[9] == ("144", "stacked", True)
    assert info[148] == ("144", "stacked", False)


def test_tabbed_layout_reported_verbatim():
    a = _leaf(1)
    b = _leaf(2)
    tabbed = _con(3, "tabbed", [1, 2], [a, b])
    tree = _workspace(8, "10", [tabbed], focus=[3])

    info = tab_info_by_leaf_id(tree.workspaces()[0])

    assert info[1] == ("3", "tabbed", True)
    assert info[2] == ("3", "tabbed", False)


def test_plain_split_layout_leaves_no_tab_group():
    a = _leaf(1)
    b = _leaf(2)
    split = _con(3, "splith", [1, 2], [a, b])
    tree = _workspace(8, "10", [split])

    info = tab_info_by_leaf_id(tree.workspaces()[0])

    assert info[1] == (None, None, False)
    assert info[2] == (None, None, False)


def test_nested_stacked_inside_a_split_branch_of_an_outer_stacked():
    # Mirrors the real ws30 case: outer stacked has 3 branches
    # (firefox, kitty, and a splitv branch containing another kitty
    # plus an INNER stacked container holding just Obsidian). Each
    # leaf's tab_group_id is its NEAREST enclosing group, not the
    # outer one — see tab_info_by_leaf_id's own docstring for why
    # that's a deliberate, documented limit, not a bug.
    firefox = _leaf(136, app_id="firefox")
    impala = _leaf(132, app_id="kitty", name="impala")
    mc = _leaf(134, app_id="kitty", name="mc")
    obsidian = _leaf(138, app_id="md.Obsidian")

    branch_firefox = _con(143, "splith", [136], [firefox])
    branch_impala = _con(140, "splith", [132], [impala])

    inner_stacked = _con(137, "stacked", [142], [_con(142, "splitv", [138], [obsidian])])
    mc_wrapper = _con(141, "splitv", [134], [mc])
    branch_mixed = _con(135, "splith", [141, 137], [mc_wrapper, inner_stacked])

    outer_stacked = _con(133, "stacked", [135, 140, 143], [branch_mixed, branch_impala, branch_firefox])
    tree = _workspace(128, "30", [outer_stacked], focus=[133])

    info = tab_info_by_leaf_id(tree.workspaces()[0])

    assert info[firefox["id"]] == ("133", "stacked", False)
    assert info[impala["id"]] == ("133", "stacked", False)
    assert info[mc["id"]] == ("133", "stacked", True)
    # Nearest group wins: obsidian's group is the INNER stacked (137),
    # not the outer one (133) — trivially active since it's the sole
    # child of its own group.
    assert info[obsidian["id"]] == ("137", "stacked", True)


def test_floating_window_is_never_part_of_a_tab_group():
    floating_leaf = _leaf(5)
    tree = Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "nodes": [{
            "id": 2, "type": "workspace", "num": 1, "name": "1",
            "rect": {"x": 6, "y": 6, "width": 1908, "height": 1041},
            "focus": [],
            "nodes": [], "floating_nodes": [floating_leaf],
        }],
    }, None, None)

    info = tab_info_by_leaf_id(tree.workspaces()[0])

    assert info[5] == (None, None, False)


# ---------- parse_tree() wiring ----------

def test_parse_tree_populates_tab_fields_on_window():
    code = _leaf(9, app_id="code")
    firefox = _leaf(148, app_id="firefox")
    wrapper_code = _con(149, "splith", [9], [code])
    wrapper_firefox = _con(150, "splith", [148], [firefox])
    stacked = _con(144, "stacked", [149, 150], [wrapper_code, wrapper_firefox])
    tree = _workspace(8, "10", [stacked], focus=[144])

    state = parse_tree(tree)
    windows = {w.app_id: w for r in state.regions for w in r.windows}

    assert windows["code"].tab_group_id == "144"
    assert windows["code"].tab_group_layout == "stacked"
    assert windows["code"].tab_active is True
    assert windows["firefox"].tab_active is False


def test_parse_tree_ordinary_window_has_no_tab_group():
    state = parse_tree(_workspace(8, "10", [_leaf(9)]))
    window = state.regions[0].windows[0]

    assert window.tab_group_id is None
    assert window.tab_group_layout is None
    assert window.tab_active is False
