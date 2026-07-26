"""Sway provider — translates sway's IPC tree into tuicc's generic model."""

from i3ipc import Connection

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider


class SwayProvider(Provider):
    def __init__(self):
        self.conn = Connection()

    def get_state(self) -> WMState:
        tree = self.conn.get_tree()
    
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
                x = (leaf.rect.x - ws_rect.x) / ws_rect.width
                y = (leaf.rect.y - ws_rect.y) / ws_rect.height
                w = leaf.rect.width / ws_rect.width
                h = leaf.rect.height / ws_rect.height
    
                windows.append(Window(
                    id=str(leaf.id),
                    app_id=leaf.app_id or leaf.window_class or "unknown",
                    title=leaf.name or "",
                    focused=leaf.focused,
                    rect=(x, y, w, h),
                ))
    
            regions.append(Region(
                id=str(workspace.num),
                name=workspace.name,
                windows=windows,
                focused=(workspace.num == focused_ws_num),
            ))
    
        focused_region_id = str(focused_ws_num) if focused_ws_num is not None else None
    
        return WMState(regions=regions, focused_region_id=focused_region_id)
