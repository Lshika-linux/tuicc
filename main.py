"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import sys
import locale

locale.setlocale(locale.LC_ALL, "")

sys.path.insert(0, "src")

from tuicc.config import load_config
from tuicc.providers.registry import build_provider
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import tab_order, nearest_in_direction
from tuicc.render import draw_all, collect_nav_items
from tuicc.theme_setup import setup_theme


DIRECTION_KEYS = {
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
}


def _sibling_in_same_group(ordered, current, direction):
    same_kind = [item for item in ordered if item.target_kind == current.target_kind]
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


def _region_item_for_focus(ordered, focus_id):
    for item in ordered:
        if item.target_kind == "region" and item.focus_target == focus_id:
            return item
    return None


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    provider = build_provider(cfg.provider_name)

    selected_id = None
    focus_id = None

    while True:
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        if focus_id is None:
            focus_id = state.focused_region_id

        items = collect_nav_items(cfg.layout, boxes, state, focus_id)
        ordered = tab_order(items, mode=cfg.tab_order)

        still_valid = any(item.id == selected_id for item in ordered)
        if not still_valid:
            match = None
            for item in ordered:
                if item.target_kind == "region" and item.focus_target == state.focused_region_id:
                    match = item
                    break
            if match is not None:
                selected_id = match.id
            elif ordered:
                selected_id = ordered[0].id
            else:
                selected_id = None

        selected_item = None
        for item in ordered:
            if item.id == selected_id:
                selected_item = item
                break

        draw_all(stdscr, cfg.layout, boxes, state, selected_id, focus_id, theme_pairs)
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord("q"):
            break
        elif key == 10 and selected_item is not None:
            if selected_item.target_kind == "region":
                provider.focus_region(selected_item.focus_target)
                break
            elif selected_item.target_kind == "window":
                provider.focus_window(selected_item.focus_target)
                break
        elif key == ord("\t") and ordered:
            region_items = [item for item in ordered if item.target_kind == "region"]
            if region_items:
                current_index = 0
                for i, item in enumerate(region_items):
                    if item.id == selected_id:
                        current_index = i
                        break
                next_index = (current_index + 1) % len(region_items)
                selected_id = region_items[next_index].id
                focus_id = region_items[next_index].focus_target
        elif key in DIRECTION_KEYS and selected_item is not None:
            direction = DIRECTION_KEYS[key]

            next_item = None
            if selected_item.target_kind == "window" and direction in ("left", "right"):
                next_item = _sibling_in_same_group(ordered, selected_item, direction)

            if next_item is None and selected_item.target_kind == "window" and direction == "left":
                next_item = _region_item_for_focus(ordered, focus_id)

            if next_item is None:
                next_item = nearest_in_direction(ordered, selected_item, direction)

            if next_item is not None:
                selected_id = next_item.id
                if next_item.target_kind == "region":
                    focus_id = next_item.focus_target


if __name__ == "__main__":
    curses.wrapper(main)
