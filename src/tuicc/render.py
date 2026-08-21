"""Module registry: maps module names (from config/layout) to their
draw and nav_items functions, and collects results across all active
modules.

---
IMPORTANT: This file must never contain module-specific drawing or
nav-item logic, and must never hardcode a module's name in an if/else.

!!! Adding a new module means adding one line to MODULES and NAV_PROVIDERS,
not editing draw_all() or collect_nav_items(). !!!

PREVIEW_RENDERERS is a third, OPTIONAL registry (module_name -> a
`(stdscr, box, preview_data, theme) -> int` function) — only a module
that populates NavItem.preview_data needs an entry here; see
navigation.py's own preview_data docstring and CLAUDE/NOTES/
design-decisions.md#module-self-sufficiency-vs-preview for why this
exists as a registry rather than a field on NavItem itself.
"""

from tuicc.modules import (
    sidebar,
    sidebar_compact,
    preview,
    quick_actions,
    rwb,
    launcher,
    connectivity,
    power_menu,
    sessions,
    control,
    media,
    bars,
    sysmon,
)
from tuicc.actions import BASE_HANDLERS


MODULES = {
    "sidebar": sidebar.draw,
    "sidebar_compact": sidebar_compact.draw,
    "preview": preview.draw,
    "quick_actions": quick_actions.draw,
    "rwb": rwb.draw,
    "launcher": launcher.draw,
    "connectivity": connectivity.draw,
    "power_menu": power_menu.draw,
    "sessions": sessions.draw,
    "control": control.draw,
    "media": media.draw,
    "bars": bars.draw,
    "sysmon": sysmon.draw,
}

NAV_PROVIDERS = {
    "sidebar": sidebar.nav_items,
    "sidebar_compact": sidebar_compact.nav_items,
    "preview": preview.nav_items,
    "quick_actions": quick_actions.nav_items,
    "rwb": rwb.nav_items,
    "launcher": launcher.nav_items,
    "connectivity": connectivity.nav_items,
    "power_menu": power_menu.nav_items,
    "sessions": sessions.nav_items,
    "control": control.nav_items,
    "media": media.nav_items,
    "bars": bars.nav_items,
    "sysmon": sysmon.nav_items,
}

PREVIEW_RENDERERS = {
    "connectivity": connectivity.draw_preview,
}

ACTION_HANDLERS = dict(BASE_HANDLERS)
ACTION_HANDLERS[quick_actions.TARGET_KIND] = quick_actions.handle
ACTION_HANDLERS[power_menu.TARGET_KIND] = power_menu.handle
ACTION_HANDLERS[control.TARGET_KIND] = control.handle
ACTION_HANDLERS.update(connectivity.HANDLERS)
ACTION_HANDLERS.update(sessions.HANDLERS)
ACTION_HANDLERS.update(media.HANDLERS)
ACTION_HANDLERS.update(sysmon.HANDLERS)

def draw_all(stdscr, layout, boxes, ctx):
    for module_box in layout.boxes:
        draw_fn = MODULES.get(module_box.name)
        if draw_fn is None:
            continue
        x, y, w, h = boxes[module_box.name]
        if w <= 0 or h <= 0:
            # A flexible box squeezed out entirely by its neighbors —
            # see layout_engine.py's compute_boxes() docstring. Nothing
            # to draw into, so skip the module outright rather than
            # handing it a degenerate box every draw()/draw_preview()
            # would otherwise need to defend against individually.
            continue
        draw_fn(stdscr, boxes[module_box.name], ctx, module_box.name)


def collect_nav_items(layout, boxes, ctx):
    items = []
    for module_box in layout.boxes:
        nav_fn = NAV_PROVIDERS.get(module_box.name)
        if nav_fn is None:
            continue
        x, y, w, h = boxes[module_box.name]
        if w <= 0 or h <= 0:
            # Not drawn this frame (see draw_all above) — not
            # navigable either, same reasoning.
            continue
        items.extend(nav_fn(boxes[module_box.name], ctx, module_box.name))
    return items
