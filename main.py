"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import sys

sys.path.insert(0, "src")

from tuicc.config import load_config
from tuicc.providers.registry import build_provider
from tuicc.layout_engine import compute_boxes
from tuicc.render import draw_all, collect_nav_items


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)

    cfg = load_config()
    provider = build_provider(cfg.provider_name)

    selected_index = 0

    while True:
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        nav_items = collect_nav_items(cfg.layout, boxes, state)

        if nav_items:
            selected_index = selected_index % len(nav_items)
            selected_id = nav_items[selected_index].id
        else:
            selected_id = None

        draw_all(stdscr, cfg.layout, boxes, state, selected_id)
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord("q"):
            break
        elif key == ord("\t"):
            selected_index += 1


if __name__ == "__main__":
    curses.wrapper(main)
