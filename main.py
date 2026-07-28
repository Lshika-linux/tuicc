"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import sys

sys.path.insert(0, "src")

from tuicc.config import load_config
from tuicc.providers.registry import build_provider
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import tab_order
from tuicc.render import draw_all, collect_nav_items
from tuicc.theme_setup import setup_theme

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    provider = build_provider(cfg.provider_name)

    selected_id = None

    while True:
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        items = collect_nav_items(cfg.layout, boxes, state)
        ordered = tab_order(items, mode=cfg.tab_order)

        still_valid = any(item.id == selected_id for item in ordered)
        if not still_valid:
            match = None
            for item in ordered:
                if item.focus_target == state.focused_region_id:
                    match = item
                    break
            if match is not None:
                selected_id = match.id
            elif ordered:
                selected_id = ordered[0].id
            else:
                selected_id = None

        focus_id = None
        for item in ordered:
            if item.id == selected_id:
                focus_id = item.focus_target
                break

        draw_all(stdscr, cfg.layout, boxes, state, selected_id, focus_id)
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord("q"):
            break
        elif key == 10 and focus_id is not None:
            provider.focus_region(focus_id)
            break
        elif key == ord("\t") and ordered:
            current_index = 0
            for i, item in enumerate(ordered):
                if item.id == selected_id:
                    current_index = i
                    break
            next_index = (current_index + 1) % len(ordered)
            selected_id = ordered[next_index].id
        
if __name__ == "__main__":
    curses.wrapper(main)
