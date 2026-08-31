"""Stacked/tabbed container detection (GitHub issue #8) — shared by
providers/sway.py and providers/i3.py, since it's genuinely WM-agnostic:
con.layout/con.focus/con.nodes/con.floating_nodes are standard i3ipc Con
fields, identical on both protocols (i3 defined this tree shape first,
sway kept it). Same "one shared pure function, thin provider-specific
wrappers around it" pattern wm_config_parser.get_wm_config() already
uses for get_config().
"""


def tab_info_by_leaf_id(node, group_id=None, group_layout=None, active=True, out=None) -> dict:
    """id(int) -> (tab_group_id, tab_group_layout, tab_active) for every
    leaf reachable under node — see Window.tab_group_id's own docstring
    (model.py) for what these mean and the known "nested groups" limit.

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
        # tab_group_id first.
        out[node.id] = (group_id, group_layout, active if group_id is not None else False)
        return out

    if node.layout in ("stacked", "tabbed"):
        active_child_id = node.focus[0] if node.focus else None
        for child in node.nodes:
            tab_info_by_leaf_id(
                child, str(node.id), node.layout, child.id == active_child_id, out
            )
    else:
        for child in node.nodes:
            tab_info_by_leaf_id(child, group_id, group_layout, active, out)

    # Floating windows are never part of a tiling stacked/tabbed group —
    # always their own thing, regardless of what encloses them.
    for child in node.floating_nodes:
        tab_info_by_leaf_id(child, None, None, True, out)

    return out
