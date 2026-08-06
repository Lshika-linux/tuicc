"""RenderContext: everything a module might need to draw itself or
report nav items, bundled into one object.

---
IMPORTANT: This exists so adding a new module never requires changing
draw()/nav_items() signatures across every existing module. A module
takes only what it needs from ctx and ignores the rest.
"""

from dataclasses import dataclass, field


@dataclass
class RenderContext:
    state: object
    selected_id: str | None
    focus_id: str | None
    theme: dict
    config: object
    pending_confirm: object = None
    active_module: str | None = None
    typing_mode: bool = False
    search_query: str = ""
    search_selected_index: int = 0
    wifi_networks: list = field(default_factory=list)
    bluetooth_devices: list = field(default_factory=list)
    connectivity: object = None
    selected_item: object = None
    # {target_region: [app_id, ...]} for whichever session slot is
    # currently expanded in the Sessions module (None if none is, or
    # that slot has nothing saved) — main.py reads sessions.py's own
    # expanded_preview() once per frame and threads it through here so
    # sidebar.py can render a while-you're-looking preview of what LOAD
    # would actually spawn and where, the same "modules talk only
    # through main.py-computed values" pattern focus_id already uses
    # for preview.py, not sidebar.py reaching into sessions.py directly.
    session_preview: dict | None = None
