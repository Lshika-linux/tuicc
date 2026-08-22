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

AUTO_FH_PROVIDERS is a fourth, OPTIONAL registry (module_name -> a
`(cfg) -> int` function) — only a module whose box can use `fh_auto`
needs an entry; see layout.py's own ModuleBox.fh_auto docstring for
the full model this implements, and each listed module's own
required_fh() for what it actually counts.
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

AUTO_FH_PROVIDERS = {
    "control": control.required_fh,
    "connectivity": connectivity.required_fh,
    "media": media.required_fh,
    "sessions": sessions.required_fh,
    "rwb": rwb.required_fh,
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


def apply_auto_fh(layout, cfg):
    """For every box with fh_auto set, recomputes its fh from
    AUTO_FH_PROVIDERS[box.name](cfg) — see layout.py's own
    ModuleBox.fh_auto docstring for the "auto until resize_step()
    touches it" model this is one half of (the other half,
    freezing fh_auto to False on an actual height resize, lives in
    resize_mode.py). Called once after cfg is fully built — app_setup.py's
    build_app() right after load_config(), and main.py's
    do_cycle_preset() (F4) after loading the newly-selected preset —
    never from inside config.py itself, since required_fh() needs a
    real, finished Config (control_toggles, visible_slots, weather
    fields), which doesn't exist yet partway through building one.
    A module name with no AUTO_FH_PROVIDERS entry is silently skipped
    (fh_auto only makes sense on the 5 modules that actually have
    list-shaped, config-driven content — nothing stops a preset from
    setting it elsewhere, but there's nothing to compute).
    """
    for box in layout.boxes:
        if not box.fh_auto:
            continue
        provider = AUTO_FH_PROVIDERS.get(box.name)
        if provider is not None:
            box.fh = provider(cfg)


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
