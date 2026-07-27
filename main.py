"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import sys

sys.path.insert(0, "src")

from tuicc.config import load_config
from tuicc.providers.registry import build_provider
from tuicc.layout_engine import compute_boxes
from tuicc.render import draw_all

def main(stdscr):
    curses.curs_set(0)

    cfg = load_config()
    provider = build_provider(cfg.provider_name)

    term_height, term_width = stdscr.getmaxyx()
    boxes = compute_boxes(cfg.layout, term_width, term_height)

    state = provider.get_state()

    draw_all(stdscr, cfg.layout, boxes, state)

    stdscr.refresh()
    stdscr.getch()


if __name__ == "__main__":
    curses.wrapper(main)
