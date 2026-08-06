"""Session save/restore module: save the current desktop layout to a
numbered slot, or load/delete a previously saved one.

Two rows of NavItems, not a "confirm a slot, then see a hidden sub-menu"
flow — which mode (LOAD/SAVE/DELETE) is active lives as a plain
module-level variable, the same pattern launcher.py's _apps_cache uses
for state that's private to one module and nothing else needs to know
about. Pressing Enter on a mode item just changes which one is lit;
pressing Enter on a slot item performs whichever mode is currently lit,
on that slot — so both rows are always visible and navigable at once,
no hidden state main.py or any other module needs to reach into.
"""

import curses
import shlex

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines, centered_x
from tuicc.keybinds import key_label
from tuicc.session import capture_session, save_session, load_session, SESSIONS_DIR

SLOT_COUNT = 3
MODES = ["load", "save", "delete"]

_active_mode = "load"


def _session_path(slot):
    return SESSIONS_DIR / f"{slot}.toml"


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Sessions")

    if ctx.pending_confirm is not None and ctx.pending_confirm.get("module") == module_name:
        confirm_text = ctx.pending_confirm.get("confirm_text")
        hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
        lines = []
        if confirm_text:
            lines.append((confirm_text, theme.get("urgent", 0)))
        lines.append((hint, theme.get("text", 0)))
        draw_centered_lines(stdscr, box, lines)
        return

    mode_row = y + 1
    slot_row = y + 2 if h > 3 else y + 1
    inner_w = max(w - 2, 0)

    # Color roles: dim = off and not selected. Plain text (bold if lit)
    # = either the lit/active mode on its own, or the cursor resting on
    # an off one. selected = the cursor is on the mode that's ALSO lit
    # — a distinct combined state, not just "selected wins".
    mode_labels = [m.upper() for m in MODES]
    mode_line = "  ".join(mode_labels)
    cx = centered_x(x + 1, inner_w, mode_line)
    for mode, label in zip(MODES, mode_labels):
        is_selected = f"sessions:mode:{mode}" == ctx.selected_id
        is_lit = mode == _active_mode
        if is_lit and is_selected:
            color = theme.get("selected", 0)
        elif is_lit or is_selected:
            color = theme.get("text", 0)
        else:
            color = theme.get("text", 0) | curses.A_DIM
        try:
            stdscr.addstr(mode_row, cx, label, color | (curses.A_BOLD if is_lit else 0))
        except curses.error:
            pass
        cx += len(label) + 2

    # Dot shows whether the slot actually has a saved session — same
    # filled/hollow convention as connectivity.py's _connection_dot —
    # independent of the number's own selection color. "X N" as the
    # measuring placeholder since the dot is always exactly 1 cell wide.
    slot_line = " ".join(f"X {slot}" for slot in range(1, SLOT_COUNT + 1))
    cx = centered_x(x + 1, inner_w, slot_line)
    for slot in range(1, SLOT_COUNT + 1):
        is_selected = f"sessions:slot:{slot}" == ctx.selected_id
        has_file = _session_path(slot).exists()
        dot = "●" if has_file else "○"
        dot_color = theme.get("accent", 0) if has_file else (theme.get("text", 0) | curses.A_DIM)
        num_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
        try:
            stdscr.addstr(slot_row, cx, dot, dot_color)
            stdscr.addstr(slot_row, cx + 2, str(slot), num_color)
        except curses.error:
            pass
        cx += 4


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    mode_row = y + 1
    slot_row = y + 2 if h > 3 else y + 1

    items = []
    for mode in MODES:
        items.append(NavItem(
            id=f"sessions:mode:{mode}",
            rect=(x + 1, mode_row, len(mode), 1),
            focus_target=mode,
            target_kind="session_mode",
        ))
    for slot in range(1, SLOT_COUNT + 1):
        items.append(NavItem(
            id=f"sessions:slot:{slot}",
            rect=(x + 1, slot_row, 1, 1),
            focus_target=str(slot),
            target_kind="session_slot",
        ))
    return items


def handle_mode(ctx, item, cfg):
    global _active_mode
    _active_mode = item.focus_target
    return False, None


def handle_slot(ctx, item, cfg):
    slot = int(item.focus_target)
    path = _session_path(slot)

    if _active_mode == "save":
        state = ctx.provider.get_state()
        entries = capture_session(state, ctx.provider)
        save_session(entries, path)
        return False, None

    if _active_mode == "load":
        if not path.exists():
            return False, None
        entries = load_session(path)

        # Warn instead of silently piling new windows onto whatever's
        # already on a target region — but only when there's actually
        # something to overwrite, so the common case (loading onto an
        # empty desktop) doesn't need an extra confirm every time.
        occupied = {r.id for r in ctx.provider.get_state().regions if r.windows}
        targets = {e["target_region"] for e in entries}
        overlap = targets & occupied
        if overlap:
            return False, {
                "restore_entries": entries,
                # Confirming clears these regions first — "load" means
                # "this is the desktop now", not "add these on top".
                "kill_regions": list(overlap),
                "confirm_text": "Load? (overwrites your session)",
                "dismiss_after_confirm": False,
                "module": "sessions",
            }

        ctx.restore_queue.extend(entries)
        # See ActionContext.reselect_region_id's docstring — bounces
        # selection back to the sidebar's own-workspace item instead of
        # leaving it sitting in the Sessions module after a load.
        ctx.reselect_region_id = ctx.provider.get_state().focused_region_id
        return False, None

    if _active_mode == "delete":
        if not path.exists():
            return False, None
        return False, {
            "command": f"rm {shlex.quote(str(path))}",
            "shell_true": False,
            "confirm_text": f"Delete session {slot}?",
            "dismiss_after_confirm": False,
            "module": "sessions",
        }

    return False, None


HANDLERS = {
    "session_mode": handle_mode,
    "session_slot": handle_slot,
}