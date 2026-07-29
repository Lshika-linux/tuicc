"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import subprocess
import sys
import locale

locale.setlocale(locale.LC_ALL, "")

sys.path.insert(0, "src")

from tuicc.config import load_config
from tuicc.context import RenderContext
from tuicc.providers.registry import build_provider
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import tab_order, nearest_in_direction
from tuicc.render import draw_all, collect_nav_items
from tuicc.theme_setup import setup_theme
from tuicc.render import draw_all, collect_nav_items, ACTION_HANDLERS


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


def _module_of_item(item):
    return item.id.split(":")[0]


def _first_item_in_module(ordered, module_name):
    for item in ordered:
        if _module_of_item(item) == module_name:
            return item
    return None


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    provider = build_provider(cfg.provider_name)

    direction_keys = {
        cfg.keybinds["left"]: "left",
        cfg.keybinds["right"]: "right",
        cfg.keybinds["up"]: "up",
        cfg.keybinds["down"]: "down",
    }

    selected_id = None
    focus_id = None
    pending_confirm = None
    active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None

    while True:
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        if focus_id is None:
            focus_id = state.focused_region_id

        ctx = RenderContext(
            state=state,
            selected_id=selected_id,
            focus_id=focus_id,
            theme=theme_pairs,
            config=cfg,
            pending_confirm=pending_confirm,
            active_module=active_module,
        )

        items = collect_nav_items(cfg.layout, boxes, ctx)
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
            ctx.selected_id = selected_id

        selected_item = None
        for item in ordered:
            if item.id == selected_id:
                selected_item = item
                break

        draw_all(stdscr, cfg.layout, boxes, ctx)
        stdscr.refresh()

        key = stdscr.getch()
    
        if key == -1:
            continue

        if pending_confirm is not None:
            if key == ord("y"):
                subprocess.Popen(pending_confirm["command"], shell=True)
                break
            elif key == ord("n") or key == cfg.keybinds["quit"]:
                pending_confirm = None
            continue

        if key == cfg.keybinds["quit"]:
            break
        elif key == cfg.keybinds["confirm"] and selected_item is not None:
            handler = ACTION_HANDLERS.get(selected_item.target_kind)
            if handler is not None:
                should_exit, pending = handler(provider, selected_item, cfg)
                if pending is not None:
                    pending_confirm = pending
                if should_exit:
                    break

       
        elif key == cfg.keybinds["switch_module"]:
            module_names = [box.name for box in cfg.layout.boxes]
            if module_names:
                current_index = 0
                if active_module in module_names:
                    current_index = module_names.index(active_module)
                next_index = (current_index + 1) % len(module_names)
                active_module = module_names[next_index]

                first_item = _first_item_in_module(ordered, active_module)
                if first_item is not None:
                    selected_id = first_item.id
                    if first_item.target_kind == "region":
                        focus_id = first_item.focus_target
        elif key == cfg.keybinds["tab"] and ordered:
            module_items = [item for item in ordered if _module_of_item(item) == active_module]
            if module_items:
                current_index = 0
                for i, item in enumerate(module_items):
                    if item.id == selected_id:
                        current_index = i
                        break
                next_index = (current_index + 1) % len(module_items)
                selected_id = module_items[next_index].id
                if module_items[next_index].target_kind == "region":
                    focus_id = module_items[next_index].focus_target
        elif key in direction_keys and selected_item is not None:
            direction = direction_keys[key]

            next_item = None
            if selected_item.target_kind == "window" and direction in ("left", "right"):
                next_item = _sibling_in_same_group(ordered, selected_item, direction)

            if next_item is None and selected_item.target_kind == "window" and direction == "left":
                next_item = _region_item_for_focus(ordered, focus_id)

            if next_item is None:
                next_item = nearest_in_direction(ordered, selected_item, direction)

            if next_item is not None:
                selected_id = next_item.id
                active_module = _module_of_item(next_item)
                if next_item.target_kind == "region":
                    focus_id = next_item.focus_target


if __name__ == "__main__":
    curses.wrapper(main)
