"""Stacked/tabbed container detection (GitHub issue #8) — shared by
providers/sway.py and providers/i3.py, since it's genuinely WM-agnostic:
con.layout/con.focus/con.nodes/con.floating_nodes are standard i3ipc Con
fields, identical on both protocols (i3 defined this tree shape first,
sway kept it). Same "one shared pure function, thin provider-specific
wrappers around it" pattern wm_config_parser.get_wm_config() already
uses for get_config().
"""


def tab_info_by_leaf_id(node, group_id=None, group_layout=None, slot_id=None, active=True, out=None) -> dict:
    """id(int) -> (tab_group_id, tab_group_layout, tab_slot_id, tab_active)
    for every leaf reachable under node — see Window.tab_group_id's own
    docstring (model.py) for what these mean and the known "nested
    groups" limit.

    group_id/group_layout/active describe the NEAREST enclosing
    stacked/tabbed ancestor, inherited top-down; entering a NEW
    stacked/tabbed container replaces them outright (nearest wins, not
    a list) — a leaf several stacked/tabbed containers deep only ever
    reports its immediate group, not whether some OUTER group is also
    hiding the whole branch it's in. Known limitation, not a bug: the
    common case (issue #8's own reports) is one level of grouping, and
    getting that exactly right is what actually matters; correctly
    resolving arbitrarily nested tab-in-a-tab visibility is real,
    separate work with no live report asking for it yet.

    slot_id is DIFFERENT from group_id — it identifies which of the
    group's own DIRECT children (a real WM-level "slot": one bar/tab
    row) a leaf sits under, not which group. It's set exactly once,
    the moment recursion enters a stacked/tabbed container's own
    child, to that child's own con id — and then carried unchanged
    through any further ordinary (non-stacked/tabbed) nesting below
    it. Found live, GitHub issue #8 follow-up (2026-08-31): a stack's
    own ACTIVE slot can itself be a real, multi-window split (e.g.
    splitting a new terminal open right next to an editor while that
    editor's stack slot is focused) — without slot_id, every leaf
    under that split would look like an independent, directly-
    switchable stack member with its own bar, when in reality they're
    all just one slot's own ordinary nested content, fully visible
    side by side, not hidden behind anything. preview.py's
    _group_tiled_windows() buckets leaves by (group_id, slot_id) so
    a multi-window slot's leaves stay together as one unit instead of
    each becoming a phantom extra row.

    node.focus (i3ipc's own field — the container's children, ordered
    by recency, most-recently-focused first) is what identifies the
    actually-visible child of a stacked/tabbed container: focus[0].
    Confirmed live against real nested sway data, not assumed from
    protocol docs alone.

    Works unchanged on i3 trees too, including i3's own floating_con
    wrapper (a leaf-less-looking container with exactly one real child)
    — it's just another non-stacked/non-tabbed node to recurse through
    like any other, so the real leaf underneath still ends up keyed by
    its own id, matching what each provider's own _leaf_to_window()
    already expects (i3.py's _unwrap_floating() unwraps the same way).
    """
    if out is None:
        out = {}
    if not node.nodes and not node.floating_nodes:
        # active is meaningless outside a group (see Window.tab_active's
        # own docstring) — reported as False there, not whatever value
        # happened to be inherited, so a stray leftover True can never
        # look like a real signal to a caller that forgets to check
        # tab_group_id first. slot_id stays None right alongside it for
        # the same reason — meaningless outside a group.
        out[node.id] = (
            group_id, group_layout, slot_id if group_id is not None else None,
            active if group_id is not None else False,
        )
        return out

    if node.layout in ("stacked", "tabbed"):
        active_child_id = node.focus[0] if node.focus else None
        for child in node.nodes:
            tab_info_by_leaf_id(
                child, str(node.id), node.layout, str(child.id), child.id == active_child_id, out
            )
    else:
        for child in node.nodes:
            tab_info_by_leaf_id(child, group_id, group_layout, slot_id, active, out)

    # Floating windows are never part of a tiling stacked/tabbed group —
    # always their own thing, regardless of what encloses them.
    for child in node.floating_nodes:
        tab_info_by_leaf_id(child, None, None, None, True, out)

    return out
