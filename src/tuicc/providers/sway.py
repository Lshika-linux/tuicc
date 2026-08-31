"""Sway provider — translates sway's IPC tree into tuicc's generic model."""

import os
import subprocess

from i3ipc import Connection

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider
from tuicc.wm_config_parser import get_wm_config


# The mark mark_self() applies to tuicc's own window, so parse_tree() can
# filter it back out — otherwise tuicc would list itself as a window in
# its own sidebar/preview.
#
# This is a PREFIX, not a fixed mark, because sway/i3 marks must be
# globally unique across the whole tree — "each identifier can only be
# set on a single window at a time" (sway(5)). A shared literal string
# breaks the moment a second tuicc instance runs: its mark_self() call
# would silently STEAL the mark away from the first instance (marks
# move, they don't duplicate), so the first instance would immediately
# stop filtering itself out. Suffixing with the process's own PID makes
# each instance's mark genuinely unique, so multiple tuicc windows can
# coexist without stealing each other's mark — and since filtering below
# checks the prefix, not an exact match, every tuicc instance still
# correctly excludes every OTHER tuicc window too, not just its own.
MARK_PREFIX = "_tuicc_self_"


def _leaf_to_window(leaf, ws_rect, floating, tab_info=None):
    x = (leaf.rect.x - ws_rect.x) / ws_rect.width
    y = (leaf.rect.y - ws_rect.y) / ws_rect.height
    w = leaf.rect.width / ws_rect.width
    h = leaf.rect.height / ws_rect.height

    group_id, group_layout, active = tab_info if tab_info else (None, None, False)
    return Window(
        id=str(leaf.id),
        app_id=leaf.app_id or leaf.window_class or "unknown",
        title=leaf.name or "",
        focused=leaf.focused,
        rect=(x, y, w, h),
        floating=floating,
        pid=leaf.pid,
        tab_group_id=group_id,
        tab_group_layout=group_layout,
        tab_active=active,
    )


def _tab_info_by_leaf_id(node, group_id=None, group_layout=None, active=True, out=None) -> dict:
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
            _tab_info_by_leaf_id(
                child, str(node.id), node.layout, child.id == active_child_id, out
            )
    else:
        for child in node.nodes:
            _tab_info_by_leaf_id(child, group_id, group_layout, active, out)

    # Floating windows are never part of a tiling stacked/tabbed group —
    # always their own thing, regardless of what encloses them.
    for child in node.floating_nodes:
        _tab_info_by_leaf_id(child, None, None, True, out)

    return out


def _is_tuicc_self(leaf) -> bool:
    return any(m.startswith(MARK_PREFIX) for m in leaf.marks)


def _stale_self_marks(tree) -> list:
    """(window_id, mark) pairs for every _tuicc_self_<pid> mark whose
    embedded pid does NOT match the window it's actually sitting on —
    the checkable signature of mark_self()'s own focus-race fallback
    mismarking an unrelated window (see that method's own docstring,
    and cleanup_stale_self_marks() below). The pid embedded in a
    LEGITIMATE tuicc self-mark is always that tuicc process's own pid
    — since sway reports each window's real owning pid directly
    (unlike i3, see that provider's own comment on this), a real
    mismatch here is unambiguous: no liveness probing needed, no
    guessing. Pure function (walks a tree, returns data, no IPC) so
    it's testable against a synthetic/recorded tree, same as
    parse_tree() itself — cleanup_stale_self_marks() is the thin,
    IPC-issuing wrapper around this.
    """
    stale = []

    def walk(node):
        for mark in node.marks:
            if mark.startswith(MARK_PREFIX):
                try:
                    mark_pid = int(mark[len(MARK_PREFIX):])
                except ValueError:
                    continue  # not this format at all — not ours to touch
                if node.pid != mark_pid:
                    stale.append((str(node.id), mark))
        for child in node.nodes + node.floating_nodes:
            walk(child)

    walk(tree)
    return stale


def parse_tree(tree) -> WMState:
    """Convert an i3ipc tree into tuicc's generic WMState.
    
    Pure function: no IPC, no side effects. Takes an i3ipc Con node so it
    can be tested against recorded fixtures without a running compositor.
    """
    focused_leaf = tree.find_focused()
    focused_ws_num = None
    if focused_leaf:
        ws = focused_leaf.workspace()
        if ws:
            focused_ws_num = ws.num

    regions = []
    for workspace in tree.workspaces():
        windows = []
        ws_rect = workspace.rect
        tab_info = _tab_info_by_leaf_id(workspace)

        for leaf in workspace.leaves():
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=False, tab_info=tab_info.get(leaf.id)))

        for leaf in workspace.floating_nodes:
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=True, tab_info=tab_info.get(leaf.id)))

        regions.append(Region(
            id=str(workspace.num),
            name=workspace.name,
            windows=windows,
            focused=(workspace.num == focused_ws_num),
        ))

    focused_region_id = str(focused_ws_num) if focused_ws_num is not None else None
    return WMState(regions=regions, focused_region_id=focused_region_id)


class SwayProvider(Provider):
    def __init__(self, conn=None):
        self.conn = conn or Connection()

    def focus_region(self, region_id: str) -> None:
        self.conn.command(f"workspace {region_id}")

    def focus_window(self, window_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] focus")

    def move_window_to_region(self, window_id: str, region_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] move container to workspace number {region_id}")

    def close_window(self, window_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] kill")

    def mark_self(self, app_id: str | None = None) -> None:
        mark = f"{MARK_PREFIX}{os.getpid()}"
        if app_id:
            # Marks by WM criteria — no focus-timing assumption at all,
            # so this is immune to the race the fallback below has.
            self.conn.command(f'[app_id="{app_id}"] mark --add {mark}')
            return
        # KNOWN LIMITATION (fallback only, when no self_app_id is
        # configured): assumes tuicc's own window is whichever one is
        # currently focused at call time — see
        # CLAUDE/NOTES/known-limitations.md#mark-self-focus-race for the
        # concrete failure mode and why setting self_app_id sidesteps it
        # entirely (see defaults/config.toml's own comment on it).
        self.conn.command(f"mark --add {mark}")

    def cleanup_stale_self_marks(self) -> None:
        tree = self.conn.get_tree()
        for window_id, mark in _stale_self_marks(tree):
            self.conn.command(f"[con_id={window_id}] unmark {mark}")

    def dismiss_self(self) -> None:
        self.conn.command(f"[con_mark={MARK_PREFIX}{os.getpid()}] move scratchpad")

    def focus_self(self, fullscreen: bool = False, force_relayout: bool = False) -> None:
        if fullscreen and force_relayout:
            # See Provider.focus_self()'s docstring: forces sway to run
            # a real layout pass for tuicc's own workspace, which is
            # otherwise suppressed the entire time tuicc stays
            # fullscreen — the fix for a sibling window landing there
            # and never getting a real rect computed for it.
            action = "focus, fullscreen disable, fullscreen enable"
        elif fullscreen:
            action = "focus, fullscreen enable"
        else:
            action = "focus"
        self.conn.command(f"[con_mark={MARK_PREFIX}{os.getpid()}] {action}")

    def no_focus_next_window(self, pid: int) -> None:
        self.conn.command(f"for_window [pid={pid}] no_focus")

    def get_state(self) -> WMState:
        return parse_tree(self.conn.get_tree())

    def set_floating_geometry(self, window_id: str, region_id: str, rect: tuple[float, float, float, float]) -> None:
        workspace = next((w for w in self.conn.get_tree().workspaces() if str(w.num) == region_id), None)
        if workspace is None:
            return

        rx, ry, rw, rh = rect
        x = round(workspace.rect.x + rx * workspace.rect.width)
        y = round(workspace.rect.y + ry * workspace.rect.height)
        w = round(rw * workspace.rect.width)
        h = round(rh * workspace.rect.height)

        self.conn.command(
            f"[con_id={window_id}] floating enable, "
            f"resize set {w}px {h}px, move position {x}px {y}px"
        )

    def wm_config(self):
        return get_wm_config(self.conn)

    def copy_to_clipboard(self, text: str) -> bool:
        """wl-copy — the standard Wayland clipboard CLI (wl-clipboard),
        not a sway-specific tool, but sway is always Wayland so this is
        the right choice for this provider. Missing binary/any
        subprocess failure both just return False — see Provider's own
        docstring for why this degrades rather than raising.
        """
        try:
            subprocess.run(["wl-copy"], input=text.encode(), check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False
