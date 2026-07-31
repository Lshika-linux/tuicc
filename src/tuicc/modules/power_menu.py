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
import subprocess

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, format_shortcut


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
        answer_hint = "Y/N"
        row = y + 1 + h // 3
        try:
            if confirm_text:
                stdscr.addstr(row, x + 2, confirm_text[:max(w - 4, 0)], theme.get("urgent", 0))
                stdscr.addstr(row + 1, x + 2, answer_hint[:max(w - 4, 0)], theme.get("text", 0))
            else:
                stdscr.addstr(row, x + 2, answer_hint[:max(w - 4, 0)], theme.get("text", 0))
        except curses.error:
            pass
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

    items = []
    for i, action in enumerate(actions):
        row = y + 1 + i
        if row >= y + h - 1:
            break
        items.append(NavItem(
            id=f"power_menu:{i}",
            rect=(x + 1, row, w - 2, 1),
            focus_target=action["command"],
            target_kind="power_action",
        ))
    return items


TARGET_KIND = "power_action"


def handle(ctx, item, cfg):
    action_index = int(item.id.split(":")[1])
    action = cfg.power_menu_actions[action_index]
    if action["confirm"]:
        return False, action
    subprocess.Popen(
        action["command"], shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True, None
