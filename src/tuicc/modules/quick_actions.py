"""Quick actions module: user-defined commands, run on Enter.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses
import subprocess

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, centered_x

ITEM_HEIGHT = 3


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    actions = ctx.config.quick_actions

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)

    draw_box_outline(stdscr, y, x, h, w, outer_color)

    if ctx.pending_confirm is not None:
        label = ctx.pending_confirm["label"]
        question = f"Run {label}?"
        answer_hint = "Y/N"

        dialog_w = min(max(len(question), len(answer_hint)) + 4, w - 2)
        dialog_h = 4
        dialog_x = x + max((w - dialog_w) // 2, 0)
        dialog_y = y + max((h - dialog_h) // 2, 0)

        question_x = centered_x(dialog_x, dialog_w, question)
        hint_x = centered_x(dialog_x, dialog_w, answer_hint)

        try:
            stdscr.addstr(dialog_y + 1, question_x, question[:max(dialog_w, 0)], theme.get("urgent", 0))
            stdscr.addstr(dialog_y + 2, hint_x, answer_hint[:max(dialog_w, 0)], theme.get("text", 0))
        except curses.error:
            pass
        return

    for i, action in enumerate(actions):
        item_y = y + 1 + i * ITEM_HEIGHT
        is_selected = f"quick_actions:{i}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)
        text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)

        draw_box_outline(stdscr, item_y, x + 1, ITEM_HEIGHT, w - 2, border_color)

        if action["icon"]:
            label = action["icon"] + " " + action["label"]
        else:
            label = action["label"]

        inner_w = w - 2
        label_x = centered_x(x + 1, inner_w, label)
        try:
            stdscr.addstr(item_y + 1, label_x, label[:max(inner_w - 2, 0)], text_color)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    actions = ctx.config.quick_actions

    items = []
    for i, action in enumerate(actions):
        item_y = y + 1 + i * ITEM_HEIGHT
        items.append(NavItem(
            id=f"quick_actions:{i}",
            rect=(x + 1, item_y, w - 2, ITEM_HEIGHT),
            focus_target=action["command"],
            target_kind="action",
        ))
    return items


TARGET_KIND = "action"


def handle(ctx, item, cfg):
    action_index = int(item.id.split(":")[1])
    action = cfg.quick_actions[action_index]
    if action["confirm"]:
        return False, action
    subprocess.Popen(action["command"], shell=True)
    return True, None
