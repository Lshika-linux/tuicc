"""Tiled/stacked/tabbed layout tree — capture, shared by providers/sway.py
and providers/i3.py (same "one shared pure function both providers
import" pattern tab_groups.py already established for the same
underlying reason: sway/i3 share the exact same i3ipc wire protocol and
Con shape, so there's nothing provider-specific about the tree walk
itself, only about which IPC calls surround it).

WMState/Region/Window (model.py) can't represent this at all —
Region.windows is a flat list, deliberately generic across every WM
tuicc might ever support, most of which have no split-tree concept in
the first place. capture_tiled_tree() works with the WM's own raw
i3ipc `Con` tree instead, walked fresh at save time — never cached,
never routed through WMState — exactly the same "read the WM's own IPC
directly, outside the generic Provider contract" precedent
wm_config_parser.py already set for parsing config text.

    Con (raw IPC tree)  -> capture_tiled_tree()  -> plain nested dict
                                                     (saved verbatim in
                                                      winrestore.py's
                                                      own TOML, [region.tiled])

Restoring this tree is NOT this module's job any more (see
CLAUDE/NOTES/design-decisions.md#append-layout-doesnt-exist-on-sway):
an earlier version of this module also converted the saved dict into
`append_layout`'s own JSON shape (layout_to_append_json()/
_regex_escape()) — deleted outright once `append_layout` turned out to
be a permanent, sway-maintainer-refused i3-only feature that never
worked on sway at all. Restoring the tree now happens by issuing plain
`move`/`layout` IPC commands directly, addressed by real con id, once
each leaf has actually spawned and matched — see winrestore.py's
TreeBuildState/advance_tree_build() and set_container_layout() below.

set_container_layout() is a plain shared free function, not a method
on capture_tiled_tree's own "walk a Con" shape, because it never reads
a Con tree structurally the way capture does — it just issues one IPC
command and re-reads a single con's own parent id afterward, identical
between sway/i3 (both speak the exact same i3ipc `layout`/`con_id`
vocabulary here — unlike mark_self()'s app_id=/class= criteria split,
addressing by con_id needs no per-provider translation at all).
list_container_groups()/move_window_to_group() below are a second,
independent restore-adjacent pair added later (see CLAUDE/NOTES/
design-decisions.md#launcher-placement-mode) for a different feature:
launching a single new window into an EXISTING stacked/tabbed group,
named (S1/T1/...) the way a real sway/i3 user would recognize from
"mod4+S made this a stack". Unlike set_container_layout() (which always
groups with whatever's currently on a workspace — correct for "make a
NEW stack/tab here"), this needs to target one SPECIFIC group among
several — `layout` has no such addressing, but sway/i3's own
`move window to mark <mark>` does (confirmed live: precisely joins the
marked container, zero focus disturbance, unaffected by how many other
groups exist on the same workspace).
"""

import os

# {"type": "window", "app_id": str}  -- a leaf, one real window
# {"type": "split", "layout": "splith"|"splitv"|"stacked"|"tabbed",
#  "children": [Node, ...]}          -- a container, >=1 real child

# get_tree() reports a stacked container's own layout as "stacked", but
# the SET command (`layout ...`) only accepts "stacking" for that same
# arrangement — confirmed live, not a typo: `layout stacked` is a hard
# IPC parse error ("Expected 'layout default|tabbed|stacking|splitv|
# splith'..."), `layout stacking` is the real keyword. Every other
# value is spelled identically both ways.
_LAYOUT_SET_KEYWORD = {"stacked": "stacking"}


def _layout_set_keyword(layout: str) -> str:
    return _LAYOUT_SET_KEYWORD.get(layout, layout)


def set_container_layout(conn, window_id: str, layout: str) -> str | None:
    """Groups window_id together with whatever else currently shares its
    parent container into a new (or extended) container of the given
    layout, and returns that container's OWN con id — a stable handle
    the caller can use to address the whole group from then on (move
    it as one unit, or fold a further sibling into it), not just this
    one member.

    Confirmed live (see CLAUDE/NOTES/design-decisions.md
    #append-layout-doesnt-exist-on-sway): `[con_id=X] layout <type>`
    wraps ALL of X's current siblings (not just X) into a brand new
    parent — this is real i3/sway behavior every WM tree tool relies
    on interactively (select several windows, hit the layout keybind),
    not something specific to tuicc's own usage. The command reply
    alone doesn't hand back the new container's id, so this re-queries
    get_tree() and reads window_id's own CURRENT parent id straight
    after — also confirmed live: once a group already exists, a
    window moved onto its workspace auto-joins that same group,
    adopting its established layout, WITHOUT needing this function
    called again — see winrestore.py's TreeBuildState, which only
    calls this once per NEW group (going from 1 loose member to 2),
    never for a 3rd+ member.

    Returns None on any failure (the command itself failed, or
    window_id no longer resolves to anything in a fresh tree) — the
    caller's own abort path handles that; this function never raises.
    """
    reply = conn.command(f"[con_id={window_id}] layout {_layout_set_keyword(layout)}")
    if not reply or not reply[0].success:
        return None
    con = conn.get_tree().find_by_id(int(window_id))
    if con is None or con.parent is None:
        return None
    return str(con.parent.id)


def _leaf_app_id(con) -> str:
    """con.app_id (sway's own native Wayland identity) falling back to
    con.window_class (i3's only identity — X11 has no app_id concept
    at all, so this attribute is simply always empty there — and
    sway's own XWayland-backed windows, which also lack a real
    app_id). One shared rule, no per-provider branching needed: sway's
    own _leaf_to_window() already uses this exact same priority
    (providers/sway.py), i3's cons just never have app_id populated so
    they fall through to window_class for free.
    """
    return con.app_id or con.window_class or "unknown"


def capture_tiled_tree(con) -> dict | None:
    """con's own tiled subtree (con.nodes only — never con.floating_nodes,
    floating windows stay on winrestore.py's existing flat/per-window
    capture path entirely, this function never sees them) as a plain
    nested dict, or None for a genuinely empty tiled area (an all-
    floating or altogether empty workspace) — winrestore.py's own
    "save" branch falls back to flat per-window capture for a region
    when this comes back None, same as when the provider doesn't
    implement get_tiled_tree() at all.

    con.layout carries through verbatim ("splith"/"splitv"/"stacked"/
    "tabbed") — deliberately NOT collapsed to just splith/splitv the
    way tileroot's own equivalent (ipc_i3.cpp::node_to_layout) does,
    losing stacked/tabbed groups entirely. tuicc already tracks this
    exact distinction elsewhere (Window.tab_group_layout, model.py —
    GitHub issue #8), and restore needs it verbatim too:
    set_container_layout() below issues the real `layout <type>` IPC
    command with this exact value (translated stacked->stacking, its
    one read-vs-set spelling quirk) — losing the distinction here would
    mean every restored group came back as a plain split, never
    stacked/tabbed.

    A split with exactly one real child collapses to that child alone
    — a lone window nested one level down under an otherwise-empty
    "splith" is noise, not real structure (same simplification
    tileroot's own node_to_layout makes).
    """
    return _walk(con)


def _walk(con) -> dict | None:
    children = [c for c in (_walk(child) for child in con.nodes) if c is not None]
    if not con.nodes:
        # A leaf: a real window, no further nodes beneath it. A con
        # with no nodes AND no identity (shouldn't happen for a real
        # window leaf, but see the module's own "never guess" convention)
        # is simply not captured.
        app_id = _leaf_app_id(con)
        if not app_id or app_id == "unknown":
            return None
        return {"type": "window", "app_id": app_id}
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return {"type": "split", "layout": con.layout, "children": children}


def flatten_leaves(tree: dict) -> list[str]:
    """Every window leaf's own app_id in tree, depth-first left-to-right
    order. Used two ways: winrestore.py's flat fallback path for a
    trivial single-window tree (nothing to group, so this is the whole
    entry list), and, more generally, anywhere a tree needs reducing to
    "just the set of apps in it" regardless of structure. Order doesn't
    materially matter to either caller, but depth-first left-to-right
    is a deterministic order tests can assert against.
    """
    if tree["type"] == "window":
        return [tree["app_id"]]
    leaves = []
    for child in tree["children"]:
        leaves.extend(flatten_leaves(child))
    return leaves


def list_container_groups(workspace) -> list[dict]:
    """workspace's own TOP-LEVEL stacked/tabbed groups (direct children
    of workspace only — NOT recursive, unlike capture_tiled_tree()): a
    group nested deeper than the top level simply isn't offered as a
    "launch into" target, a documented limitation matching the real,
    common case (mod4+S on a single focused window always produces a
    top-level group) rather than a crash or a wrong answer. Each entry
    is `{"label": "S1"/"S2"/"T1"/..., "container_id": str(node.id)}` —
    "S" for stacked, "T" for tabbed, numbered independently per kind in
    on-screen (workspace.nodes) order. An empty list means "no existing
    group to join" — the launcher's own placement picker then only
    offers tiled/stack_new/tab_new/floating.
    """
    counters = {"stacked": 0, "tabbed": 0}
    prefixes = {"stacked": "S", "tabbed": "T"}
    groups = []
    for node in workspace.nodes:
        if node.layout in counters:
            counters[node.layout] += 1
            groups.append({"label": f"{prefixes[node.layout]}{counters[node.layout]}", "container_id": str(node.id)})
    return groups


_group_mark_counter = 0


def move_window_to_group(conn, window_id: str, container_id: str) -> bool:
    """Moves window_id to join container_id specifically — not just
    "whatever's on this workspace" the way set_container_layout()'s own
    grouping does, a real distinction once more than one group can
    exist on the same workspace (S1 vs T1). Marks container_id with a
    fresh, disposable, globally-unique temp mark (pid+counter, same
    uniqueness reasoning MARK_PREFIX gives for tuicc's own self-mark —
    marks must be unique across the whole WM, and multiple tuicc
    instances could be doing this at once), `move window to mark
    <mark>` (confirmed live: joins the marked container directly,
    adopting its layout, with zero WM focus disturbance), then removes
    the mark again — it was only ever needed for this one addressing
    step. Returns whether the actual move succeeded; a failed initial
    mark also returns False (nothing to clean up in that case — the
    mark was never applied). False across the board (mark, move, or
    unmark all no-op safe to skip on an already-gone window) is the
    caller's cue to fall back to `tiled`, not raise.
    """
    global _group_mark_counter
    _group_mark_counter += 1
    mark = f"_tuicc_group_target_{os.getpid()}_{_group_mark_counter}"
    mark_reply = conn.command(f"[con_id={container_id}] mark --add {mark}")
    if not mark_reply or not mark_reply[0].success:
        return False
    move_reply = conn.command(f"[con_id={window_id}] move window to mark {mark}")
    conn.command(f"[con_id={container_id}] unmark {mark}")
    return bool(move_reply) and move_reply[0].success
