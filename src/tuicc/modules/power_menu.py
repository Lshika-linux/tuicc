"""Power menu module: a compact 2x2 grid of icon tiles for
lock/logout/reboot/shutdown-style actions, with uptime shown below.

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
from tuicc.render_utils import draw_box_outline


GRID_COLS = 4
CELL_HEIGHT = 4      # icon box (3 rows: border/icon/border) + label row (1 row)
ICON_BOX_W = 6        # chars wide — terminal chars are ~2x taller than wide,
ICON_BOX_H = 3         # so a wider-than-tall box reads as visually square


def _read_uptime_seconds():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _format_uptime(seconds):
    if seconds is None:
        return "uptime unavailable"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"uptime: {hours:02d}:{minutes:02d}:{secs:02d}"


def _cell_rect(box, index):
    x, y, w, h = box
    col = index % GRID_COLS
    row = index // GRID_COLS
    cell_w = (w - 2) // GRID_COLS
    cell_x = x + 1 + col * cell_w
    cell_y = y + 1 + row * CELL_HEIGHT
    return cell_x, cell_y, cell_w, CELL_HEIGHT


def _icon_box_rect(cell_x, cell_y, cell_w):
    box_w = min(ICON_BOX_W, cell_w)  # clamp so it never exceeds the cell on a narrow terminal
    box_x = cell_x + max((cell_w - box_w) // 2, 0)
    return box_x, cell_y, box_w, ICON_BOX_H


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    actions = ctx.config.power_menu_actions[:4]

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

    for i, action in enumerate(actions):
        cell_x, cell_y, cell_w, cell_h = _cell_rect(box, i)
        is_selected = f"power_menu:{i}" == ctx.selected_id
        border_color = theme.get("selected", 0) if is_selected else theme.get("border", 0)

        icon_box_x, icon_box_y, icon_box_w, icon_box_h = _icon_box_rect(cell_x, cell_y, cell_w)
        draw_box_outline(stdscr, icon_box_y, icon_box_x, icon_box_h, icon_box_w, border_color)

        icon = action.get("icon") or "?"
        label = action["label"]
        text_color = theme.get("urgent", 0) if action.get("confirm") else theme.get("text", 0)
        if is_selected:
            text_color = theme.get("selected", 0)

        icon_x = icon_box_x + max((icon_box_w - len(icon)) // 2, 0)
        label_trunc = label[:max(cell_w - 2, 0)]
        label_x = cell_x + max((cell_w - len(label_trunc)) // 2, 0)

        try:
            stdscr.addstr(icon_box_y + 1, icon_x, icon, text_color | curses.A_BOLD)
            stdscr.addstr(cell_y + ICON_BOX_H, label_x, label_trunc, text_color)
        except curses.error:
            pass

    grid_rows = (len(actions) + GRID_COLS - 1) // GRID_COLS
    uptime_row = y + 1 + grid_rows * CELL_HEIGHT
    if uptime_row < y + h - 1:
        uptime_text = _format_uptime(_read_uptime_seconds())
        text_x = x + 1 + max((w - 2 - len(uptime_text)) // 2, 0)
        try:
            stdscr.addstr(uptime_row, text_x, uptime_text[:max(w - 2, 0)], theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    actions = ctx.config.power_menu_actions[:4]
    items = []
    for i, action in enumerate(actions):
        cell_x, cell_y, cell_w, cell_h = _cell_rect(box, i)
        items.append(NavItem(
            id=f"power_menu:{i}",
            rect=(cell_x, cell_y, cell_w, cell_h),
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
