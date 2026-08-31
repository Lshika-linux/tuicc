"""Tests for stacked/tabbed container handling (GitHub issue #8) on the
i3 provider — Window.tab_group_id/tab_group_layout/tab_active, populated
by tab_groups.tab_info_by_leaf_id() and wired into providers/i3.py's
parse_tree()/_leaf_to_window(), same mechanism as sway (see
test_sway_provider_tabs.py) since tab_info_by_leaf_id() is itself
WM-agnostic. These tests exist to confirm i3's own quirks — the
floating_con wrapper, window_class instead of app_id — don't break that
sharing, not to re-test tab_info_by_leaf_id()'s own logic (already
covered on the sway side).
"""

from i3ipc import Con

from tuicc.providers.i3 import parse_tree


def _leaf(id_, window_class="kitty", name="w"):
    return {
        "id": id_, "type": "con", "app_id": None,
        "window_properties": {"class": window_class},
        "name": name, "focused": False, "marks": [], "window": id_,
        "rect": {"x": 6, "y": 66, "width": 953, "height": 961},
        "nodes": [], "floating_nodes": [],
    }


def _con(id_, layout, focus, nodes):
    return {
        "id": id_, "type": "con", "app_id": None, "window_class": None,
        "name": None, "layout": layout, "focus": focus,
        "rect": {"x": 6, "y": 6, "width": 1908, "height": 1041},
        "nodes": nodes, "floating_nodes": [],
    }


def _floating_wrapper(id_, leaf):
    # i3 wraps every floating window in a floating_con container that
    # carries none of the window's own properties — see
    # providers/i3.py's _unwrap_floating(). tab_info_by_leaf_id() has to
    # see through this the same way _unwrap_floating() does, without any
    # i3-specific code of its own — that's exactly what this test file
    # exists to confirm.
    return {
        "id": id_, "type": "floating_con", "marks": [],
        "rect": leaf["rect"],
        "nodes": [leaf], "floating_nodes": [],
    }


def _workspace(id_, name, nodes, focus=None, floating_nodes=None):
    return Con({
        "id": id_, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "nodes": [{
            "id": id_ + 1, "type": "workspace", "num": int(name), "name": name,
            "rect": {"x": 6, "y": 6, "width": 1908, "height": 1041},
            "focus": focus or [],
            "nodes": nodes, "floating_nodes": floating_nodes or [],
        }],
    }, None, None)


def test_parse_tree_populates_tab_fields_on_window():
    code = _leaf(9, window_class="Code")
    firefox = _leaf(148, window_class="firefox")
    wrapper_code = _con(149, "splith", [9], [code])
    wrapper_firefox = _con(150, "splith", [148], [firefox])
    stacked = _con(144, "stacked", [149, 150], [wrapper_code, wrapper_firefox])
    tree = _workspace(8, "10", [stacked], focus=[144])

    state = parse_tree(tree)
    windows = {w.app_id: w for r in state.regions for w in r.windows}

    assert windows["Code"].tab_group_id == "144"
    assert windows["Code"].tab_group_layout == "stacked"
    assert windows["Code"].tab_active is True
    assert windows["firefox"].tab_active is False


def test_parse_tree_tabbed_layout_reported_verbatim():
    a = _leaf(1, window_class="a")
    b = _leaf(2, window_class="b")
    tabbed = _con(3, "tabbed", [1, 2], [a, b])
    tree = _workspace(8, "10", [tabbed], focus=[3])

    state = parse_tree(tree)
    windows = {w.app_id: w for r in state.regions for w in r.windows}

    assert windows["a"].tab_group_layout == "tabbed"
    assert windows["a"].tab_active is True
    assert windows["b"].tab_active is False


def test_parse_tree_ordinary_window_has_no_tab_group():
    state = parse_tree(_workspace(8, "10", [_leaf(9)]))
    window = state.regions[0].windows[0]

    assert window.tab_group_id is None
    assert window.tab_group_layout is None
    assert window.tab_active is False


def test_floating_window_through_floating_con_wrapper_has_no_tab_group():
    # The real regression case: tab_info_by_leaf_id() is keyed by leaf
    # id, and i3's floating_con wrapper has its OWN, different id — if
    # parse_tree() looked up tab_info by the wrapper's id instead of the
    # unwrapped leaf's, this would silently look like "no info" (None
    # default) for the wrong reason. Confirmed here it's None for the
    # RIGHT reason: floating windows are never part of a tab group.
    leaf = _leaf(5, window_class="firefox")
    wrapper = _floating_wrapper(500, leaf)
    tree = _workspace(8, "10", [], floating_nodes=[wrapper])

    state = parse_tree(tree)
    window = state.regions[0].windows[0]

    assert window.app_id == "firefox"
    assert window.tab_group_id is None
    assert window.tab_group_layout is None
    assert window.tab_active is False


def test_floating_window_inside_a_stacked_workspace_is_unaffected():
    # A stacked tiled group coexisting with a floating window on the
    # same workspace — the floating one must stay untouched by the
    # tiled group's own tab_group_id, and vice versa.
    tiled_a = _leaf(9, window_class="Code")
    tiled_b = _leaf(148, window_class="firefox")
    wrapper_a = _con(149, "splith", [9], [tiled_a])
    wrapper_b = _con(150, "splith", [148], [tiled_b])
    stacked = _con(144, "stacked", [149, 150], [wrapper_a, wrapper_b])

    floating_leaf = _leaf(5, window_class="pavucontrol")
    floating_wrapper = _floating_wrapper(500, floating_leaf)

    tree = _workspace(8, "10", [stacked], focus=[144], floating_nodes=[floating_wrapper])

    state = parse_tree(tree)
    windows = {w.app_id: w for r in state.regions for w in r.windows}

    assert windows["Code"].tab_group_id == "144"
    assert windows["pavucontrol"].tab_group_id is None
    assert windows["pavucontrol"].floating is True
