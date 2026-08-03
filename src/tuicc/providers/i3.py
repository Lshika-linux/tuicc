"""i3 provider — translates i3's IPC tree into tuicc's generic model."""

import os

from i3ipc import Connection
from Xlib import display, X

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider


_NET_WM_PID = "_NET_WM_PID"


def _x11_pid_for_window(xid: int) -> int | None:
    """PID via the standard _NET_WM_PID EWMH property, read directly
    from the X server. i3's own IPC tree has no pid field (see
    _leaf_to_window's comment below) — this is the fallback Provider.
    resolve_pid() uses instead, since i3 runs on X11 and well-behaved
    X11 clients set this hint themselves. Best-effort: broad exception
    handling is deliberate here, matching resolve_pid()'s contract
    (None means "couldn't find out", never an error) — X11 failure
    modes (no DISPLAY, window already closed, client didn't set the
    hint) are numerous and not this function's job to distinguish.
    """
    try:
        d = display.Display()
        atom = d.intern_atom(_NET_WM_PID)
        window = d.create_resource_object("window", xid)
        prop = window.get_full_property(atom, X.AnyPropertyType)
    except Exception:
        return None
    if prop is None or not prop.value:
        return None
    return int(prop.value[0])


# The mark mark_self() applies to tuicc's own window, so parse_tree() can
# filter it back out — otherwise tuicc would list itself as a window in
# its own sidebar/preview.
#
# This is a PREFIX, not a fixed mark, because sway/i3 marks must be
# globally unique across the whole tree — "each identifier can only be
# set on a single window at a time" (sway(5), shared by i3's IPC).  A
# shared literal string breaks the moment a second tuicc instance runs:
# its mark_self() call would silently STEAL the mark away from the first
# instance (marks move, they don't duplicate), so the first instance
# would immediately stop filtering itself out. Suffixing with the
# process's own PID makes each instance's mark genuinely unique, so
# multiple tuicc windows can coexist without stealing each other's mark
# — and since filtering below checks the prefix, not an exact match,
# every tuicc instance still correctly excludes every OTHER tuicc window
# too, not just its own.
MARK_PREFIX = "_tuicc_self_"


def _unwrap_floating(node):
    """i3 wraps every floating window in a floating_con container that
    carries no window properties of its own — the real window is its
    single child. Sway flattens this away; i3 does not, so we undo it
    here to keep the rest of the parsing code identical to sway's.
    """
    if node.type == "floating_con" and node.nodes:
        return node.nodes[0]
    return node


def _leaf_to_window(leaf, ws_rect, floating):
    x = (leaf.rect.x - ws_rect.x) / ws_rect.width
    y = (leaf.rect.y - ws_rect.y) / ws_rect.height
    w = leaf.rect.width / ws_rect.width
    h = leaf.rect.height / ws_rect.height

    return Window(
        id=str(leaf.id),
        app_id=leaf.window_class or leaf.app_id or "unknown",
        title=leaf.name or "",
        focused=leaf.focused,
        rect=(x, y, w, h),
        floating=floating,
        # i3's GET_TREE has no "pid" property (unlike sway's) — verified
        # against i3's own IPC docs. Window.pid stays at its None default;
        # code that needs a pid on i3 goes through I3Provider.resolve_pid()
        # instead (X11 lookup, on demand — not cheap enough for every
        # window on every frame, see that method's docstring).
    )


def _is_tuicc_self(leaf) -> bool:
    return any(m.startswith(MARK_PREFIX) for m in leaf.marks)


def parse_tree(tree) -> WMState:
    """Convert an i3ipc tree into tuicc's generic WMState.

    Pure function: no IPC, no side effects. Takes an i3ipc Con node so it
    can be tested against recorded fixtures without a running window manager.
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

        # i3's workspace.leaves() includes floating windows as well as
        # tiled ones, unlike sway's. Resolve floating windows first so we
        # can skip them when walking leaves(), avoiding double-counting.
        floating_leaves = [_unwrap_floating(n) for n in workspace.floating_nodes]
        floating_ids = {leaf.id for leaf in floating_leaves}

        for leaf in workspace.leaves():
            if leaf.id in floating_ids:
                continue
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=False))

        for leaf in floating_leaves:
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

class I3Provider(Provider):
    def __init__(self, conn=None):
        self.conn = conn or Connection()

    def focus_region(self, region_id: str) -> None:
        self.conn.command(f"workspace number {region_id}")

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
            # so this is immune to the race the fallback below has. i3
            # is X11-only, no "app_id" concept — its criteria keyword is
            # "class" (see _leaf_to_window mapping window_class into the
            # generic model's app_id field for the same reason).
            self.conn.command(f'[class="{app_id}"] mark --add {mark}')
            return
        # KNOWN LIMITATION (fallback only, when no self_app_id is
        # configured): this assumes tuicc's own window is the one
        # currently focused at call time. Launching several instances in
        # rapid, back-to-back succession (not normal keypress-paced usage)
        # can race — a not-yet-focused instance could mark a DIFFERENT
        # instance's window as "itself". The PID suffix above prevents
        # mark collisions, not this timing assumption; fixing it would
        # need identifying "my own window" independent of focus timing
        # (e.g. matching the hosting terminal's PID), which is real
        # complexity for a scenario nobody hits under normal use.
        self.conn.command(f"mark --add {mark}")

    def dismiss_self(self) -> None:
        self.conn.command(f"[con_mark={MARK_PREFIX}{os.getpid()}] move scratchpad")

    def get_state(self) -> WMState:
        return parse_tree(self.conn.get_tree())

    def resolve_pid(self, window_id: str) -> int | None:
        leaf = self.conn.get_tree().find_by_id(int(window_id))
        if leaf is None or leaf.window is None:
            return None
        return _x11_pid_for_window(leaf.window)

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
