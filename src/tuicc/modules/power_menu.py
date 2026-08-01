"""Power menu module: a vertical list of lock/logout/reboot/shutdown-style
actions (one per row).

Reads from its own [power_menu] config section, not [quick_actions] —
quick_actions.py is a separate, generic module left unused in the
current layout, reserved for whatever it becomes later. This module
owns its own data source on purpose, so the two don't end up coupled
just because they happen to look similar today.

---
IMPORTANT: Each module owns both how it draws itself and where its own focusable
items are — the core never guesses a module's internal layout.
"""

import curses

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, format_shortcut, draw_centered_lines
from tuicc.keybinds import key_label
from tuicc.actions import spawn_detached


def _row_label(action):
    if action.get("shortcut"):
        return f"{format_shortcut(action['shortcut'])} {action['label']}"
    return action["label"]


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    actions = ctx.config.power_menu_actions

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Power Menu")

    if ctx.pending_confirm is not None:
        confirm_text = ctx.pending_confirm.get("confirm_text")
        hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
        lines = []
        if confirm_text:
            lines.append((confirm_text, theme.get("urgent", 0)))
        lines.append((hint, theme.get("text", 0)))
        draw_centered_lines(stdscr, box, lines)
        return

    inner_w = max(w - 4, 0)
    for i, action in enumerate(actions):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        is_selected = f"power_menu:{i}" == ctx.selected_id
        text_color = theme.get("urgent", 0) if action.get("confirm") else theme.get("text", 0)
        if is_selected:
            text_color = theme.get("selected", 0)

        label = _row_label(action)
        try:
            stdscr.addstr(row, x + 2, label[:inner_w], text_color | (curses.A_BOLD if is_selected else 0))
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    actions = ctx.config.power_menu_actions
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
            id=f"power_menu:{i}",
            rect=(x + 1, row, w - 2, 1),
            focus_target=action["command"],
            target_kind="power_action",
            preview_text=preview_text,
        ))
    return items


TARGET_KIND = "power_action"


def handle(ctx, item, cfg):
    action_index = int(item.id.split(":")[1])
    action = cfg.power_menu_actions[action_index]
    if action["confirm"]:
        return False, action
    spawn_detached(action["command"], action["shell_true"])
    return True, None
