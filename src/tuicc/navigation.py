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


def tab_order(items: list[NavItem], mode: str = "columns_first") -> list[NavItem]:
    if mode == "columns_first":
        key = lambda item: (item.rect[0], item.rect[1])
    elif mode == "rows_first":
        key = lambda item: (item.rect[1], item.rect[0])
    else:
        raise ValueError(f"Unknown tab_order mode: {mode!r}. Expected 'columns_first' or 'rows_first'.")

    return sorted(items, key=key)


def nearest_in_direction(items: list[NavItem], current: NavItem, direction: str) -> NavItem | None:
    cx, cy = current.rect[0], current.rect[1]

    candidates = []
    for item in items:
        if item is current:
            continue

        ix, iy = item.rect[0], item.rect[1]

        if direction == "right" and ix > cx:
            candidates.append(item)
        elif direction == "left" and ix < cx:
            candidates.append(item)
        elif direction == "down" and iy > cy:
            candidates.append(item)
        elif direction == "up" and iy < cy:
            candidates.append(item)

    if not candidates:
        return None

    def score(item):
        ix, iy = item.rect[0], item.rect[1]
        y_diff = abs(iy - cy)
        distance = ((ix - cx) ** 2 + (iy - cy) ** 2) ** 0.5
        return (y_diff, distance)

    return min(candidates, key=score)


def hotkey_map(items: list[NavItem]) -> dict[str, NavItem]:
    result = {}
    for item in items:
        if item.hotkey is not None:
            if item.hotkey in result:
                raise ValueError(f"Duplicate hotkey {item.hotkey!r}: used by both {result[item.hotkey].id!r} and {item.id!r}")
            result[item.hotkey] = item

    return result
