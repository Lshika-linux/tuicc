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
