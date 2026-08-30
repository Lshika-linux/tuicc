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
    # None (not just an empty list) is a real value here, not just the
    # unset default — see status_worker.py's StatusWorker.get() and
    # modules/connectivity.py's _build_rows: it means the last poll for
    # that domain errored (or hasn't completed yet), distinct from a
    # genuinely empty list ("no networks around" vs "couldn't check").
    wifi_networks: list | None = field(default_factory=list)
    bluetooth_devices: list | None = field(default_factory=list)
    wifi_error: str | None = None
    bluetooth_error: str | None = None
    # The shared status_worker.StatusWorker instance — named `status`,
    # not `connectivity`, despite wifi/bluetooth being its first two
    # domains: audio/brightness/control-toggle domains (VISION.md's
    # R5) register against this exact same worker, one thread for
    # everything, not a separate one per module. ActionContext.status
    # (actions.py) is the same object, same name, for handlers — same
    # "two same-named, different-typed ctx objects" split every other
    # per-frame/per-action value in tuicc already uses.
    status: object = None
    selected_item: object = None
    # render.py's PREVIEW_RENDERERS dict (module_name -> render
    # function), threaded through here rather than imported directly by
    # preview.py — render.py imports every module including preview.py
    # itself to build MODULES, so preview.py importing back from
    # render.py would be circular. frame_update.py (which already
    # imports render.py.collect_nav_items with no such problem) threads
    # it through once per frame instead, same pattern active_module/
    # selected_item already use. See navigation.py's own preview_data
    # docstring for what this is for.
    preview_renderers: dict = field(default_factory=dict)
    # {target_region: [app_id, ...]} for whichever session slot is
    # currently expanded in the Sessions module (None if none is, or
    # that slot has nothing saved) — main.py reads sessions.py's own
    # expanded_preview() once per frame and threads it through here so
    # sidebar.py can render a while-you're-looking preview of what LOAD
    # would actually spawn and where, the same "modules talk only
    # through main.py-computed values" pattern focus_id already uses
    # for preview.py, not sidebar.py reaching into sessions.py directly.
    session_preview: dict | None = None
    # {(toggle_index, state_index): curses_color_pair} for every
    # [[control.toggle.state]] with an explicit `color` — built once at
    # startup by theme_setup.assign_control_toggle_pairs(), not
    # per-frame (curses.init_pair() is a one-time setup call, same
    # reasoning theme_pairs above is built once and passed through, not
    # rebuilt in the render loop). A state with no `color` configured
    # is simply absent here; modules/control.py falls back to a theme
    # default for it, same "look it up, fall back if missing" pattern
    # _connection_dot already uses.
    control_colors: dict = field(default_factory=dict)
    # The shared media.cava.CavaReader instance (or None if the current
    # preset has no media module at all) — same "expose the live worker
    # object itself, not a snapshot" convention `status` above uses;
    # modules/media.py calls .get_frame()/.get_error()/.is_running()
    # directly. Genuinely optional, unlike `status`: a CavaReader only
    # exists because the media module specifically needs one, whereas
    # StatusWorker is wired up unconditionally in main.py regardless of
    # which modules a preset actually uses.
    cava: object = None
    # wm_config_parser.WmConfigInfo (or None) — the WM's own config,
    # parsed once at startup (app_setup.py) and threaded through here
    # unchanged every frame after, same "built once, not per-frame"
    # convention control_colors above already uses (this one for a
    # different reason: control_colors is a one-time curses setup call,
    # this one is a real IPC round-trip + regex parse over the WM's
    # whole config text, which only ever changes on a WM config reload
    # — see Provider.wm_config()'s own docstring). modules/sidebar.py's
    # _build_slots() is the main reader — see GitHub issue #9.
    wm_config: object = None
