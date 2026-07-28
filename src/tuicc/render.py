"""Module registry: maps module names (from config/layout) to their
draw and nav_items functions, and collects results across all active
modules.

---
IMPORTANT: This file must never contain module-specific drawing or
nav-item logic, and must never hardcode a module's name in an if/else.

!!! Adding a new module means adding one line to MODULES and NAV_PROVIDERS,
not editing draw_all() or collect_nav_items(). !!!
"""

from tuicc.modules import sidebar, preview, quick_actions


MODULES = {
    "sidebar": sidebar.draw,
    "preview": preview.draw,
    "quick_actions": quick_actions.draw,
}

NAV_PROVIDERS = {
    "sidebar": sidebar.nav_items,
    "preview": preview.nav_items,
    "quick_actions": quick_actions.nav_items,
}


def draw_all(stdscr, layout, boxes, ctx):
    for module_box in layout.boxes:
        draw_fn = MODULES.get(module_box.name)
        if draw_fn is None:
            continue
        draw_fn(stdscr, boxes[module_box.name], ctx)


def collect_nav_items(layout, boxes, ctx):
    items = []
    for module_box in layout.boxes:
        nav_fn = NAV_PROVIDERS.get(module_box.name)
        if nav_fn is None:
            continue
        items.extend(nav_fn(boxes[module_box.name], ctx))
    return items
