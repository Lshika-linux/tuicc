"""Navigation model: focusable items and how to move between them.

Modules report their focusable items (a window in the sidebar, a quick
action, a power-menu entry...) as a flat list of NavItem. This module
knows nothing about which module an item belongs to, or what happens
when an item is selected — it only works with positions (rect) and
optional hotkeys.

Two ways to move between items, all operating on the same NavItem list:
  - tab order   (Tab / Shift+Tab, and duplicate keys — arrows, vim
                 hjkl): a fixed sequence, order configurable, next/prev
                 item within a module, rolling into the next/previous
                 module at either end (main.py composes this from
                 next_item_in_module/prev_item_in_module +
                 next_module_name/prev_module_name)
  - hotkey      (Ctrl+key): direct jump via a registered key

There used to be a third way, spatial (arrow-key) nearest-neighbor
search across the whole layout regardless of module boundaries — removed
for being unpredictable in practice (nearest-on-screen isn't always
nearest-in-context) and for needing real special-casing to work around
overlapping floating windows in the preview. Tab-order cycling doesn't
have that problem to begin with: it never does geometric search, so
on-screen overlap is irrelevant to it.

Wraparound (e.g. last item -> first item on Tab) is intentionally not
handled here — it depends on which item is "current", which is state
this module doesn't own. That belongs to whatever code drives the main
loop and tracks the current selection. Rolling into an adjacent module
at either end works the same way, one level up: next_item_in_module/
prev_item_in_module return None exactly when there's nowhere further to
go *within* the module, which is the caller's (main.py's) signal to
advance next_module_name/prev_module_name instead.
"""

from dataclasses import dataclass


@dataclass
class NavItem:
    id: str
    rect: tuple[float, float, float, float]  # x, y, w, h — normalized 0..1, same space as ModuleBox
    hotkey: str | None = None
    focus_target: str | None = None
    target_kind: str = "region"
    # (text, color_pair) lines a module wants shown elsewhere (e.g. in
    # preview) while this item is selected — already-resolved color,
    # same shape as render_utils.draw_centered_lines takes. A module
    # that has nothing to preview just leaves this None.
    preview_text: list[tuple[str, int]] | None = None
    # True if preview_text represents something urgent (sysmon.py's
    # diagnostics row: real failed units/OOM/errors, not "all clear") —
    # colors preview.py's whole border urgent, not just the text inside
    # it, matching how urgent/selection colors are used everywhere else
    # in this codebase. False (the default) leaves preview.py's border
    # exactly as it already was for every other module's preview_text.
    preview_urgent: bool = False


def tab_order(items: list[NavItem], mode: str = "columns_first") -> list[NavItem]:
    if mode == "columns_first":
        key = lambda item: (item.rect[0], item.rect[1])
    elif mode == "rows_first":
        key = lambda item: (item.rect[1], item.rect[0])
    else:
        raise ValueError(f"Unknown tab_order mode: {mode!r}. Expected 'columns_first' or 'rows_first'.")

    return sorted(items, key=key)


def hotkey_map(items: list[NavItem]) -> dict[str, NavItem]:
    result = {}
    for item in items:
        if item.hotkey is not None:
            if item.hotkey in result:
                raise ValueError(f"Duplicate hotkey {item.hotkey!r}: used by both {result[item.hotkey].id!r} and {item.id!r}")
            result[item.hotkey] = item

    return result


def module_of_item(item: NavItem) -> str:
    """The module name an item belongs to, derived from its id's
    "modulename:id" convention.
    """
    return item.id.split(":")[0]


def same_row_neighbor(
    items: list[NavItem], selected_id: str | None, direction: int, wrap: bool = False,
) -> NavItem | None:
    """The next item left (direction=-1) or right (+1) of the selected
    one, within the SAME module and exact row (rect's y) — lets
    Left/Right step across a module's own horizontal row (e.g.
    sessions.py's LOAD/SAVE/DEL/NAME) before falling back to jump-to-
    next-module. None if there's no selection or no row-mate (the
    common single-column case, a no-op). Narrower than the removed
    spatial nearest-neighbor search (see module docstring): same
    module, same exact row only, no ambiguity to get wrong. wrap=True
    (opt-in — see main.py's call sites) cycles instead of returning
    None at the row's end, for level-2 sessions.py-style exceptions.
    """
    if selected_id is None:
        return None
    current = next((item for item in items if item.id == selected_id), None)
    if current is None:
        return None
    module = module_of_item(current)
    row_items = sorted(
        (item for item in items if module_of_item(item) == module and item.rect[1] == current.rect[1]),
        key=lambda item: item.rect[0],
    )
    index = row_items.index(current) + direction
    if wrap:
        return row_items[index % len(row_items)]
    if 0 <= index < len(row_items):
        return row_items[index]
    return None


def first_item_in_module(items: list[NavItem], module_name: str) -> NavItem | None:
    for item in items:
        if module_of_item(item) == module_name:
            return item
    return None


def last_item_in_module(items: list[NavItem], module_name: str) -> NavItem | None:
    """Mirror of first_item_in_module — used to land on a module's LAST
    item when rolling into it backward (Shift+Tab past the first item
    of the module you were on), so backing up feels continuous instead
    of snapping to the front.
    """
    result = None
    for item in items:
        if module_of_item(item) == module_name:
            result = item
    return result


def next_module_name(module_names: list[str], active_module: str | None) -> str | None:
    """Cycle to the next module name (the module-switch keys — Right/l
    when vim_mode is on — and Tab's roll-into-next-module case),
    wrapping around. If active_module isn't in module_names (e.g.
    nothing selected yet), starts from the first one. None only if
    module_names itself is empty.
    """
    if not module_names:
        return None

    current_index = 0
    if active_module in module_names:
        current_index = module_names.index(active_module)
    next_index = (current_index + 1) % len(module_names)
    return module_names[next_index]


def prev_module_name(module_names: list[str], active_module: str | None) -> str | None:
    """Mirror of next_module_name, cycling backward."""
    if not module_names:
        return None

    current_index = 0
    if active_module in module_names:
        current_index = module_names.index(active_module)
    prev_index = (current_index - 1) % len(module_names)
    return module_names[prev_index]


def next_item_across_modules(
    items: list[NavItem], module_names: list[str], active_module: str | None, selected_id: str | None
) -> NavItem | None:
    """Tab's full behavior: next_item_in_module's result, or — once
    exhausted — the first item of the next module, walking forward
    through as many modules as it takes (wrapping via next_module_name,
    bounded to len(module_names) hops) until one actually has an item.
    A single next-module lookup isn't enough since modules with zero
    nav items are common (launcher, preview, clock) — see
    CLAUDE/GUIDE.md's "Known open issues" for the stuck-Tab bug this
    fixes. Returns None only if every module is empty.
    """
    next_item = next_item_in_module(items, active_module, selected_id)
    if next_item is not None:
        return next_item

    name = active_module
    for _ in range(len(module_names)):
        name = next_module_name(module_names, name)
        if name is None:
            return None
        candidate = first_item_in_module(items, name)
        if candidate is not None:
            return candidate
    return None


def prev_item_across_modules(
    items: list[NavItem], module_names: list[str], active_module: str | None, selected_id: str | None
) -> NavItem | None:
    """Mirror of next_item_across_modules for Shift+Tab — see that
    function's docstring for the bug this fixes.
    """
    prev_item = prev_item_in_module(items, active_module, selected_id)
    if prev_item is not None:
        return prev_item

    name = active_module
    for _ in range(len(module_names)):
        name = prev_module_name(module_names, name)
        if name is None:
            return None
        candidate = last_item_in_module(items, name)
        if candidate is not None:
            return candidate
    return None


def next_item_in_module(items: list[NavItem], module_name: str, selected_id: str | None) -> NavItem | None:
    """Next item within a single module (Tab key and duplicates), no
    wrap — None when already on the module's last item. That None is
    the caller's (main.py's) signal to roll into the next module
    instead of cycling back to this module's own first item; module
    boundaries are main.py's concern, not this function's. If
    selected_id isn't among that module's items (e.g. focus just
    switched modules), starts from the first one — same fallback this
    had before, unrelated to the no-wrap change.
    """
    module_items = [item for item in items if module_of_item(item) == module_name]
    if not module_items:
        return None

    current_index = 0
    for i, item in enumerate(module_items):
        if item.id == selected_id:
            current_index = i
            break
    next_index = current_index + 1
    if next_index >= len(module_items):
        return None
    return module_items[next_index]


def prev_item_in_module(items: list[NavItem], module_name: str, selected_id: str | None) -> NavItem | None:
    """Mirror of next_item_in_module (Shift+Tab key and duplicates), no
    wrap — None when already on the module's first item.
    """
    module_items = [item for item in items if module_of_item(item) == module_name]
    if not module_items:
        return None

    current_index = 0
    for i, item in enumerate(module_items):
        if item.id == selected_id:
            current_index = i
            break
    prev_index = current_index - 1
    if prev_index < 0:
        return None
    return module_items[prev_index]


def resolve_selection(item: NavItem, focus_id: str | None) -> tuple[str, str, str | None]:
    """The new (selected_id, active_module, focus_id) after selecting
    item. active_module always matches wherever item actually lives;
    focus_id only changes for region items, since anything else
    (a window, a power-menu action...) doesn't mean "show a different
    workspace" — it leaves whatever region was last shown untouched.
    """
    new_focus_id = item.focus_target if item.target_kind == "region" else focus_id
    return item.id, module_of_item(item), new_focus_id


def global_shortcut_item(global_shortcuts: dict, key: int) -> NavItem | None:
    """The ad-hoc NavItem for a global shortcut keypress, or None if the
    key isn't bound to one. Global shortcuts have no on-screen position
    (rect is a zero placeholder) and no region to focus — they exist
    only to carry a target_kind through to an ACTION_HANDLERS lookup,
    the same dispatch path a selected on-screen item would use.
    """
    shortcut = global_shortcuts.get(key)
    if shortcut is None:
        return None

    return NavItem(
        id=shortcut["item_id"],
        rect=(0, 0, 0, 0),
        focus_target=None,
        target_kind=shortcut["target_kind"],
    )
