"""Sway provider — translates sway's IPC tree into tuicc's generic model."""

import os

from i3ipc import Connection

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider


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


def _leaf_to_window(leaf, ws_rect, floating):
    x = (leaf.rect.x - ws_rect.x) / ws_rect.width
    y = (leaf.rect.y - ws_rect.y) / ws_rect.height
    w = leaf.rect.width / ws_rect.width
    h = leaf.rect.height / ws_rect.height

    return Window(
        id=str(leaf.id),
        app_id=leaf.app_id or leaf.window_class or "unknown",
        title=leaf.name or "",
        focused=leaf.focused,
        rect=(x, y, w, h),
        floating=floating,
        pid=leaf.pid,
    )


def _is_tuicc_self(leaf) -> bool:
    return any(m.startswith(MARK_PREFIX) for m in leaf.marks)


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

        for leaf in workspace.leaves():
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=False))

        for leaf in workspace.floating_nodes:
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=True))

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

    def mark_self(self) -> None:
            # KNOWN LIMITATION: this assumes tuicc's own window is the one
            # currently focused at call time. Launching several instances in
            # rapid, back-to-back succession (not normal keypress-paced usage)
            # can race — a not-yet-focused instance could mark a DIFFERENT
            # instance's window as "itself". The PID suffix above prevents
            # mark collisions, not this timing assumption; fixing it would
            # need identifying "my own window" independent of focus timing
            # (e.g. matching the hosting terminal's PID), which is real
            # complexity for a scenario nobody hits under normal use.
        self.conn.command(f"mark --add {MARK_PREFIX}{os.getpid()}")
   
    def get_state(self) -> WMState:
        return parse_tree(self.conn.get_tree())
