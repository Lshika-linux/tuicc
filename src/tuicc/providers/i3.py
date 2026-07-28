"""i3 provider — translates i3's IPC tree into tuicc's generic model."""

from i3ipc import Connection

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider


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
    )


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
            windows.append(_leaf_to_window(leaf, ws_rect, floating=False))

        for leaf in floating_leaves:
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

    def get_state(self) -> WMState:
        return parse_tree(self.conn.get_tree())
