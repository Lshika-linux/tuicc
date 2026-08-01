"""Quick actions module: user-defined commands, run on Enter.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines
from tuicc.keybinds import key_label
from tuicc.actions import spawn_detached


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    actions = ctx.config.quick_actions

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Quick Actions")

    if ctx.pending_confirm is not None:
        label = ctx.pending_confirm["label"]
        question = f"Run {label}?"
        hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
        lines = [
            (question, theme.get("urgent", 0)),
            (hint, theme.get("text", 0)),
        ]
        draw_centered_lines(stdscr, box, lines)
        return

    inner_w = max(w - 4, 0)
    for i, action in enumerate(actions):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        is_selected = f"quick_actions:{i}" == ctx.selected_id
        text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)

        if action["icon"]:
            label = f"{action['icon']} {action['label']}"
        else:
            label = action["label"]

        try:
            stdscr.addstr(row, x + 2, label[:inner_w], text_color | (curses.A_BOLD if is_selected else 0))
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    actions = ctx.config.quick_actions
    theme = ctx.theme or {}

    items = []
    for i, action in enumerate(actions):
        row = y + 1 + i
        if row >= y + h - 1:
            break
        preview_text = [(f"> {action['command']} <", theme.get("accent", 0))]
        if action["shell_true"]:
            preview_text.append(("!! SHELL=TRUE !!", theme.get("urgent", 0)))
        items.append(NavItem(
            id=f"quick_actions:{i}",
            rect=(x + 1, row, w - 2, 1),
            focus_target=action["command"],
            target_kind="action",
            preview_text=preview_text,
        ))
    return items


TARGET_KIND = "action"


def handle(ctx, item, cfg):
    action_index = int(item.id.split(":")[1])
    action = cfg.quick_actions[action_index]
    if action["confirm"]:
        return False, action
    spawn_detached(action["command"], action["shell_true"])
    return True, None
