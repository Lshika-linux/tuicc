"""Quick actions module: user-defined commands, run on Enter.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


def draw(stdscr, box, ctx):
    x, y, w, h = box
    theme = ctx.theme or {}
    actions = ctx.config.quick_actions

    draw_box_outline(stdscr, y, x, h, w, theme.get("border", 0))

    for i, action in enumerate(actions):
        is_selected = f"quick_actions:{i}" == ctx.selected_id
        prefix = "> " if is_selected else "  "
        if action["icon"]:
            label = prefix + action["icon"] + " " + action["label"]
        else:
            label = prefix + action["label"]
        color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
        try:
            stdscr.addstr(y + 1 + i, x + 1, label[:max(w - 2, 0)], color)
        except curses.error:
            pass


def nav_items(box, ctx) -> list[NavItem]:
    x, y, w, h = box
    actions = ctx.config.quick_actions

    items = []
    for i, action in enumerate(actions):
        items.append(NavItem(
            id=f"quick_actions:{i}",
            rect=(x, y + 1 + i, w, 1),
            focus_target=action["command"],
            target_kind="action",
        ))
    return items
