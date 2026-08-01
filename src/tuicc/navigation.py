"""Navigation model: focusable items and how to move between them.

Modules report their focusable items (a window in the sidebar, a quick
action, a power-menu entry...) as a flat list of NavItem. This module
knows nothing about which module an item belongs to, or what happens
when an item is selected — it only works with positions (rect) and
optional hotkeys.

Three ways to move between items, all operating on the same NavItem list:
  - tab order   (Tab / Shift+Tab): a fixed sequence, order configurable
  - spatial     (arrow keys): nearest item in a direction
  - hotkey      (Ctrl+key): direct jump via a registered key

Wraparound (e.g. last item -> first item on Tab) is intentionally not
handled here — it depends on which item is "current", which is state
this module doesn't own. That belongs to whatever code drives the main
loop and tracks the current selection.
"""

from dataclasses import dataclass


@dataclass
class NavItem:
    id: str
    rect: tuple[float, float, float, float]  # x, y, w, h — normalized 0..1, same space as ModuleBox
    hotkey: str | None = None
    focus_target: str | None = None
    target_kind: str = "region"


def tab_order(items: list[NavItem], mode: str = "columns_first") -> list[NavItem]:
    if mode == "columns_first":
        key = lambda item: (item.rect[0], item.rect[1])
    elif mode == "rows_first":
        key = lambda item: (item.rect[1], item.rect[0])
    else:
        raise ValueError(f"Unknown tab_order mode: {mode!r}. Expected 'columns_first' or 'rows_first'.")

    return sorted(items, key=key)


def _overlap_amount(a_start, a_end, b_start, b_end):
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def nearest_in_direction(items: list[NavItem], current: NavItem, direction: str) -> NavItem | None:
    cx0, cy0, cw, ch = current.rect
    cx1, cy1 = cx0 + cw, cy0 + ch

    candidates = []
    for item in items:
        if item is current:
            continue

        ix0, iy0, iw, ih = item.rect
        ix1, iy1 = ix0 + iw, iy0 + ih

        if direction == "right" and ix0 >= cx1:
            candidates.append(item)
        elif direction == "left" and ix1 <= cx0:
            candidates.append(item)
        elif direction == "down" and iy0 >= cy1:
            candidates.append(item)
        elif direction == "up" and iy1 <= cy0:
            candidates.append(item)

    if not candidates:
        return None

    def score(item):
        ix0, iy0, iw, ih = item.rect
        ix1, iy1 = ix0 + iw, iy0 + ih

        if direction in ("left", "right"):
            overlap = _overlap_amount(cy0, cy1, iy0, iy1)
            gap = ix0 - cx1 if direction == "right" else cx0 - ix1
        else:
            overlap = _overlap_amount(cx0, cx1, ix0, ix1)
            gap = iy0 - cy1 if direction == "down" else cy0 - iy1

        has_overlap = overlap > 0
        return (not has_overlap, gap)

    return min(candidates, key=score)


def hotkey_map(items: list[NavItem]) -> dict[str, NavItem]:
    result = {}
    for item in items:
        if item.hotkey is not None:
            if item.hotkey in result:
                raise ValueError(f"Duplicate hotkey {item.hotkey!r}: used by both {result[item.hotkey].id!r} and {item.id!r}")
            result[item.hotkey] = item

    return result
def sibling_in_same_group(items: list[NavItem], current: NavItem, direction: str) -> NavItem | None:
    """Linear left-to-right neighbor within the same target_kind — the
    predictable fallback for items that overlap (e.g. floating windows
    in the preview), where spatial search breaks down.
    """
    same_kind = [item for item in items if item.target_kind == current.target_kind]
    same_kind_sorted = sorted(same_kind, key=lambda item: item.rect[0])

    current_index = None
    for i, item in enumerate(same_kind_sorted):
        if item.id == current.id:
            current_index = i
            break

    if current_index is None:
        return None

    if direction == "right":
        next_index = current_index + 1
    elif direction == "left":
        next_index = current_index - 1
    else:
        return None

    if 0 <= next_index < len(same_kind_sorted):
        return same_kind_sorted[next_index]

    return None


def region_item_for_focus(items: list[NavItem], focus_id: str | None) -> NavItem | None:
    """The sidebar's NavItem for the workspace currently being viewed —
    used as the deterministic fallback when moving left out of the
    leftmost preview window, instead of letting spatial search land on
    an arbitrary nearby region.
    """
    for item in items:
        if item.target_kind == "region" and item.focus_target == focus_id:
            return item
    return None


def module_of_item(item: NavItem) -> str:
    """The module name an item belongs to, derived from its id's
    "modulename:id" convention.
    """
    return item.id.split(":")[0]


def first_item_in_module(items: list[NavItem], module_name: str) -> NavItem | None:
    for item in items:
        if module_of_item(item) == module_name:
            return item
    return None


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


def resolve_direction_move(
    items: list[NavItem], current: NavItem, direction: str, focus_id: str | None
) -> NavItem | None:
    """Priority order for arrow-key movement out of a window item:
    same-group sibling first (predictable left-right order, since
    overlapping floating windows break spatial search), then — only
    when moving left with no sibling — the sidebar's item for the
    currently-focused region (deterministic instead of landing on
    whichever region happens to be spatially nearest), then general
    spatial search as the final fallback. Non-window items skip
    straight to spatial search.
    """
    next_item = None
    if current.target_kind == "window" and direction in ("left", "right"):
        next_item = sibling_in_same_group(items, current, direction)

    if next_item is None and current.target_kind == "window" and direction == "left":
        next_item = region_item_for_focus(items, focus_id)

    if next_item is None:
        next_item = nearest_in_direction(items, current, direction)

    return next_item
