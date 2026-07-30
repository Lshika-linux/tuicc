"""Built-in handlers for target_kinds not owned by any specific module.

region/window aren't module-specific — any module reporting a region
or window item expects the same underlying action (focus_region /
focus_window), so these live here rather than in a module file.
Wifi/bluetooth toggle logic, by contrast, IS module-specific (only
connectivity.py knows what "toggle" means for that data), so those
handlers self-register from connectivity.py instead — same pattern
quick_actions.py uses for its own TARGET_KIND.

Handler signature: (ctx, item, cfg) -> (should_exit, pending). ctx is
an ActionContext bundling the WM provider and the connectivity
worker. should_exit=True means tuicc exits after this runs. pending,
if not None, becomes the new pending_confirm value.
"""

from dataclasses import dataclass


@dataclass
class ActionContext:
    provider: object
    connectivity: object


def _handle_region(ctx, item, cfg):
    ctx.provider.focus_region(item.focus_target)
    return True, None


def _handle_window(ctx, item, cfg):
    ctx.provider.focus_window(item.focus_target)
    return True, None


BASE_HANDLERS = {
    "region": _handle_region,
    "window": _handle_window,
}
