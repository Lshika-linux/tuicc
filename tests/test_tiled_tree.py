"""Tests for tiled_tree.py — capture_tiled_tree/flatten_leaves/
set_container_layout, all pure/thin-IPC functions. capture_tiled_tree()
is tested against real i3ipc Con objects (same discipline as
providers/sway.py's/i3.py's own parse_tree() tests) built from plain
dicts via Con(data, None, None) — no live WM/connection needed.
set_container_layout() is tested via a tiny fake connection (same
FakeConnection shape test_provider_commands.py already uses) whose
get_tree() hands back a fixed post-command tree — good enough to test
the "read the reply, then re-query and read window_id's own new parent
id" logic without a live WM.
"""

from i3ipc import Con

from tuicc.tiled_tree import (
    capture_tiled_tree,
    flatten_leaves,
    set_container_layout,
    list_container_groups,
    move_window_to_group,
)


def _leaf(id_, app_id=None, window_class=None):
    node = {
        "id": id_, "type": "con", "app_id": app_id,
        "name": "w", "focused": False, "marks": [], "pid": None,
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800}, "nodes": [], "floating_nodes": [],
    }
    if window_class is not None:
        node["window_properties"] = {"class": window_class}
    return node


def _split(id_, layout, children, floating_nodes=()):
    return {
        "id": id_, "type": "con", "layout": layout,
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": list(children), "floating_nodes": list(floating_nodes),
    }


def _con(data):
    return Con(data, None, None)


class _FakeReply:
    def __init__(self, success):
        self.success = success


class _FakeConn:
    """Minimal stand-in for i3ipc.Connection — records commands, hands
    back a fixed tree on get_tree() (simulating "the tree as it looks
    right after the command already took effect" — set_container_layout()
    never needs get_tree() before the command, only after).

    results (optional): one success bool per .command() call, popped in
    order — needed for move_window_to_group()'s own multi-command
    sequence (mark/move/unmark), where different steps may need
    different outcomes in one test. Falls back to always returning
    `success` when not given (every set_container_layout() test's own
    single-command shape).
    """
    def __init__(self, success=True, tree=None, results=None):
        self.commands = []
        self._success = success
        self._tree = tree
        self._results = list(results) if results is not None else None

    def command(self, cmd):
        self.commands.append(cmd)
        success = self._results.pop(0) if self._results else self._success
        return [_FakeReply(success)]

    def get_tree(self):
        return self._tree


# ---------- capture_tiled_tree ----------

def test_capture_tiled_tree_single_leaf():
    workspace = _con(_split(1, "splith", [_leaf(2, app_id="firefox")]))

    assert capture_tiled_tree(workspace) == {"type": "window", "app_id": "firefox"}


def test_capture_tiled_tree_falls_back_to_window_class():
    workspace = _con(_split(1, "splith", [_leaf(2, window_class="Firefox")]))

    assert capture_tiled_tree(workspace) == {"type": "window", "app_id": "Firefox"}


def test_capture_tiled_tree_real_split():
    workspace = _con(_split(1, "splith", [
        _leaf(2, app_id="firefox"),
        _leaf(3, app_id="kitty"),
    ]))

    assert capture_tiled_tree(workspace) == {
        "type": "split", "layout": "splith",
        "children": [
            {"type": "window", "app_id": "firefox"},
            {"type": "window", "app_id": "kitty"},
        ],
    }


def test_capture_tiled_tree_preserves_stacked_and_tabbed():
    # The one thing tileroot's own equivalent gets wrong (collapses
    # both to splith) — tuicc already tracks this distinction
    # elsewhere (Window.tab_group_layout) and must carry it through here.
    workspace = _con(_split(1, "stacked", [
        _leaf(2, app_id="firefox"),
        _leaf(3, app_id="kitty"),
    ]))

    assert capture_tiled_tree(workspace)["layout"] == "stacked"

    workspace = _con(_split(1, "tabbed", [
        _leaf(2, app_id="firefox"),
        _leaf(3, app_id="kitty"),
    ]))

    assert capture_tiled_tree(workspace)["layout"] == "tabbed"


def test_capture_tiled_tree_nested_splits():
    workspace = _con(_split(1, "splith", [
        _leaf(2, app_id="firefox"),
        _split(3, "splitv", [_leaf(4, app_id="kitty"), _leaf(5, app_id="code")]),
    ]))

    assert capture_tiled_tree(workspace) == {
        "type": "split", "layout": "splith",
        "children": [
            {"type": "window", "app_id": "firefox"},
            {"type": "split", "layout": "splitv", "children": [
                {"type": "window", "app_id": "kitty"},
                {"type": "window", "app_id": "code"},
            ]},
        ],
    }


def test_capture_tiled_tree_single_child_split_collapses():
    workspace = _con(_split(1, "splith", [
        _split(2, "splitv", [_leaf(3, app_id="kitty")]),
    ]))

    assert capture_tiled_tree(workspace) == {"type": "window", "app_id": "kitty"}


def test_capture_tiled_tree_empty_workspace_returns_none():
    workspace = _con(_split(1, "splith", []))

    assert capture_tiled_tree(workspace) is None


def test_capture_tiled_tree_ignores_floating_nodes():
    workspace = _con(_split(1, "splith", [_leaf(2, app_id="firefox")],
                             floating_nodes=[_leaf(3, app_id="kitty")]))

    assert capture_tiled_tree(workspace) == {"type": "window", "app_id": "firefox"}


# ---------- flatten_leaves ----------

def test_flatten_leaves_single_window():
    assert flatten_leaves({"type": "window", "app_id": "firefox"}) == ["firefox"]


def test_flatten_leaves_depth_first_left_to_right():
    tree = {
        "type": "split", "layout": "splith",
        "children": [
            {"type": "window", "app_id": "firefox"},
            {"type": "split", "layout": "splitv", "children": [
                {"type": "window", "app_id": "kitty"},
                {"type": "window", "app_id": "code"},
            ]},
        ],
    }

    assert flatten_leaves(tree) == ["firefox", "kitty", "code"]


# ---------- set_container_layout ----------

def test_set_container_layout_returns_new_parent_id():
    # Simulates the tree AFTER "[con_id=3] layout stacking" already
    # grouped 3 and 4 under a new container (id 2) — set_container_layout()
    # doesn't build this tree itself, it just reads it back.
    tree = _con(_split(1, "splith", [
        _split(2, "stacked", [_leaf(3, app_id="firefox"), _leaf(4, app_id="kitty")]),
    ]))
    conn = _FakeConn(success=True, tree=tree)

    result = set_container_layout(conn, "3", "stacked")

    assert result == "2"


def test_set_container_layout_translates_stacked_to_stacking_keyword():
    # Live-confirmed asymmetry: get_tree() reports "stacked", but the
    # SET command only accepts "stacking" — "layout stacked" is a hard
    # IPC parse error.
    conn = _FakeConn(success=True, tree=_con(_split(1, "splith", [_leaf(3, app_id="firefox")])))

    set_container_layout(conn, "3", "stacked")

    assert conn.commands == ["[con_id=3] layout stacking"]


def test_set_container_layout_leaves_other_keywords_unchanged():
    conn = _FakeConn(success=True, tree=_con(_split(1, "splith", [_leaf(3, app_id="firefox")])))

    set_container_layout(conn, "3", "tabbed")

    assert conn.commands == ["[con_id=3] layout tabbed"]


def test_set_container_layout_returns_none_on_command_failure():
    conn = _FakeConn(success=False, tree=_con(_split(1, "splith", [_leaf(3, app_id="firefox")])))

    assert set_container_layout(conn, "3", "tabbed") is None


def test_set_container_layout_returns_none_when_window_vanished():
    # window_id no longer resolves to anything in a fresh tree (e.g. it
    # closed mid-restore) — the caller's own abort path handles this.
    conn = _FakeConn(success=True, tree=_con(_split(1, "splith", [_leaf(3, app_id="firefox")])))

    assert set_container_layout(conn, "999", "tabbed") is None


# ---------- list_container_groups ----------

def test_list_container_groups_empty_workspace():
    workspace = _con(_split(1, "splith", []))

    assert list_container_groups(workspace) == []


def test_list_container_groups_ignores_plain_splits_and_loose_leaves():
    workspace = _con(_split(1, "splith", [
        _leaf(2, app_id="firefox"),
        _split(3, "splitv", [_leaf(4, app_id="kitty"), _leaf(5, app_id="code")]),
    ]))

    assert list_container_groups(workspace) == []


def test_list_container_groups_labels_stacked_and_tabbed_separately():
    workspace = _con(_split(1, "splith", [
        _split(2, "stacked", [_leaf(3, app_id="a"), _leaf(4, app_id="b")]),
        _split(5, "tabbed", [_leaf(6, app_id="c"), _leaf(7, app_id="d")]),
    ]))

    assert list_container_groups(workspace) == [
        {"label": "S1", "container_id": "2"},
        {"label": "T1", "container_id": "5"},
    ]


def test_list_container_groups_numbers_each_kind_independently():
    workspace = _con(_split(1, "splith", [
        _split(2, "stacked", [_leaf(3, app_id="a"), _leaf(4, app_id="b")]),
        _split(5, "stacked", [_leaf(6, app_id="c"), _leaf(7, app_id="d")]),
        _split(8, "tabbed", [_leaf(9, app_id="e"), _leaf(10, app_id="f")]),
    ]))

    assert [g["label"] for g in list_container_groups(workspace)] == ["S1", "S2", "T1"]


def test_list_container_groups_does_not_recurse_into_nested_groups():
    # Documented scoping — only top-level groups are offered as launch
    # targets, a group nested inside a plain split one level down isn't.
    workspace = _con(_split(1, "splith", [
        _split(2, "splitv", [_split(3, "stacked", [_leaf(4, app_id="a"), _leaf(5, app_id="b")])]),
    ]))

    assert list_container_groups(workspace) == []


# ---------- move_window_to_group ----------

def test_move_window_to_group_marks_moves_and_unmarks():
    conn = _FakeConn(success=True)

    result = move_window_to_group(conn, "10", "20")

    assert result is True
    assert len(conn.commands) == 3
    mark = conn.commands[0].split()[-1]
    assert mark.startswith("_tuicc_group_target_")
    assert conn.commands[0] == f"[con_id=20] mark --add {mark}"
    assert conn.commands[1] == f"[con_id=10] move window to mark {mark}"
    assert conn.commands[2] == f"[con_id=20] unmark {mark}"


def test_move_window_to_group_returns_false_when_mark_fails():
    conn = _FakeConn(results=[False])

    result = move_window_to_group(conn, "10", "20")

    assert result is False
    assert len(conn.commands) == 1  # never even tried the move


def test_move_window_to_group_returns_false_when_move_fails_but_still_unmarks():
    conn = _FakeConn(results=[True, False, True])

    result = move_window_to_group(conn, "10", "20")

    assert result is False
    assert len(conn.commands) == 3  # unmark still ran, cleaning up
