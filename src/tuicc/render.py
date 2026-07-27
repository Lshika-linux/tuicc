"""Rendering: draws module content onto the terminal via curses.

main.py also touches curses, but only for app lifecycle (wrapper,
cursor, terminal size) — this file is where actual module content
(boxes, text) gets drawn. Everything else (model, layout, navigation,
config) is curses-agnostic and doesn't know this file exists.

DISCLAMER: THIS PART HERE IS AI GENERATED. I DONT REALLY UNDERSTAND THE WORKINGS
OF CURSES YET, THE REST OF THE ARCHITECTURE I KNOW IN DETAIL, THIS IS AI.
"""

import curses


def draw_box_outline(stdscr, y, x, h, w):
    if h < 1 or w < 1:
        return

    try:
        stdscr.addstr(y, x, "+" + "-" * (w - 2) + "+")
        for i in range(1, h - 1):
            stdscr.addstr(y + i, x, "|")
            stdscr.addstr(y + i, x + w - 1, "|")
        stdscr.addstr(y + h - 1, x, "+" + "-" * (w - 2) + "+")
    except curses.error:
        pass


def draw_sidebar(stdscr, box, state):
    x, y, w, h = box

    draw_box_outline(stdscr, y, x, h, w)

    for i, region in enumerate(state.regions):
        label = f"[{region.id}] {region.name}"
        label = ("> " if region.focused else "  ") + label
        try:
            stdscr.addstr(y + 1 + i, x + 1, label[:max(w - 2, 0)])
        except curses.error:
            pass


def draw_preview(stdscr, box, state):
    x, y, w, h = box

    draw_box_outline(stdscr, y, x, h, w)

    focused_region = None
    for region in state.regions:
        if region.id == state.focused_region_id:
            focused_region = region

    if focused_region is None:
        return

    for window in focused_region.windows:
        rx, ry, rw, rh = window.rect

        win_x = x + 1 + round(rx * (w - 2))
        win_y = y + 1 + round(ry * (h - 2))
        win_w = round(rw * (w - 2))
        win_h = round(rh * (h - 2))

        draw_box_outline(stdscr, win_y, win_x, win_h, win_w)

        try:
            stdscr.addstr(win_y + 1, win_x + 1, window.app_id[:max(win_w - 2, 0)])
        except curses.error:
            pass

MODULES = {
    "sidebar": draw_sidebar,
    "preview": draw_preview,
}


def draw_all(stdscr, layout, boxes, state):
    for module_box in layout.boxes:
        draw_fn = MODULES.get(module_box.name)
        if draw_fn is None:
            continue
        draw_fn(stdscr, boxes[module_box.name], state)
