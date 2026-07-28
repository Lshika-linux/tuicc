"""Built-in handlers for target_kinds not owned by any specific module.

region/window aren't module-specific — any module reporting a region
or window item expects the same underlying action (focus_region /
focus_window), so these live here rather than in a module file.

Handler signature: (provider, item, cfg) -> (should_exit, pending).
should_exit=True means tuicc exits after this runs. pending, if not
None, becomes the new pending_confirm value.
"""


def _handle_region(provider, item, cfg):
    provider.focus_region(item.focus_target)
    return True, None


def _handle_window(provider, item, cfg):
    provider.focus_window(item.focus_target)
    return True, None


BASE_HANDLERS = {
    "region": _handle_region,
    "window": _handle_window,
}
