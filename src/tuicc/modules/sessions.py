"""Session save/restore module: save the current desktop layout to a
numbered slot, or load/delete a previously saved one — each slot
carrying a user-editable display name (see config.py's session_names).

Every row always shows its dot, name, AND its four actions
(LOAD/SAVE/DEL/NAME, right-aligned to the box's own edge) — the
actions just sit dimmed and unreachable by Tab until you actually
commit to that slot, not hidden. Two-level navigation underneath that,
mirroring resize_mode.py's browsing/editing split: level 1 (browsing)
is just the 3 slot rows — Tab/arrows cycle between them, Enter on one
"expands" it, lighting its actions up from dimmed to selectable and
moving the cursor onto them; level 2 (expanded) is those four actions
for the one expanded slot — Enter on one performs it (same
confirm-dialog flow as before for DELETE/overwriting LOAD). Unlike
resize_mode, this needs no dedicated hijack tier of its own for the
browsing<->expanded transition itself — nav_items() alone decides
what's navigable (and draw() alone decides what's merely dimmed vs.
truly selectable) at either level, so Tab/Shift+Tab/arrows keep
working unchanged; only Escape (to collapse back to browsing) and raw
keystrokes (while renaming) need main.py to check in, mirroring
launcher.py's typing_mode / help_mode's color editor — is_expanded()/
collapse()/is_naming()/handle_naming_key() below are the hooks main.py
calls for exactly that, the same "module owns its own mode-state,
main.py owns *when* to call into it" split resize_mode/help_mode use.

_expanded_slot/_naming_slot/_name_input are plain module-level state —
same "private state, nothing else needs to reach into" pattern
launcher.py's _apps_cache (and this module's own former _active_mode)
used. Renaming persists to config.toml's [sessions] name_<N> keys via
config.set_session_name(), same "type in place, Enter applies"
mechanism as help_mode's color editor.
"""

import curses
import shlex

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines
from tuicc.keybinds import key_label
from tuicc.session import capture_session, save_session, load_session, SESSIONS_DIR

SLOT_COUNT = 3
ACTIONS = ["load", "save", "del", "name"]
NAME_COL_WIDTH = 9  # fixed so every row's actions line up, regardless of that row's own name length

_expanded_slot = None   # int | None — which slot's actions are showing, if any
_naming_slot = None      # int | None — which slot is mid-rename, if any
_name_input = ""


def _session_path(slot):
    return SESSIONS_DIR / f"{slot}.toml"


def _slot_name(cfg, slot):
    return cfg.session_names.get(slot, f"Slot {slot}")


def _action_positions(x: int, w: int) -> list[tuple[str, int]]:
    """[(action, absolute column), ...] for the 4 actions, right-
    aligned so the block's last character sits one cell in from the
    box's right border — shared by draw() and nav_items() so a row's
    highlighted/selectable regions always match what's actually drawn,
    on every row, not just the expanded one (see draw()'s docstring on
    why every row shows all 4 actions now, not just the expanded row).
    """
    labels = [action.upper() for action in ACTIONS]
    total_width = sum(len(label) for label in labels) + (len(labels) - 1)
    cx = x + w - 1 - total_width
    positions = []
    for action, label in zip(ACTIONS, labels):
        positions.append((action, cx))
        cx += len(label) + 1
    return positions


def is_expanded() -> bool:
    return _expanded_slot is not None


def collapse() -> int | None:
    """Leaves the expanded (level 2) state, back to browsing all three
    slot rows — main.py calls this on Escape while a slot is expanded.
    Returns the slot that WAS expanded (None if none was), so the
    caller can reselect "sessions:row:<slot>" directly — same reason
    handle_row sets ActionContext.reselect_item_id: nav_items() no
    longer reports the just-selected action id the instant this
    collapses, which would otherwise trip main.py's stale-selection
    recovery into jumping to the sidebar, same bug found live for the
    expand direction.
    """
    global _expanded_slot
    slot = _expanded_slot
    _expanded_slot = None
    return slot


def is_naming() -> bool:
    return _naming_slot is not None


def start_naming(slot: int, current_name: str) -> None:
    global _naming_slot, _name_input
    _naming_slot = slot
    _name_input = current_name


def handle_naming_key(key: int) -> None:
    """Same shape as help_mode.handle_color_input_key/launcher.py's
    handle_typing_key — Enter isn't handled here (applying the name
    needs cfg state this function doesn't have), so the caller checks
    for confirm before falling back to this for everything else.
    """
    global _naming_slot, _name_input
    if key == 27:  # Escape: cancel, discard the in-progress edit
        _naming_slot = None
        _name_input = ""
        return
    if key in (curses.KEY_BACKSPACE, 127, 8):
        _name_input = _name_input[:-1]
        return
    if 32 <= key <= 126:
        _name_input += chr(key)


def apply_naming() -> tuple[int, str] | None:
    """(slot, new_name) on success. Always succeeds if a rename is in
    progress — an empty/whitespace-only name just resets back to the
    "Slot N" default (see config.py's session_names loading), there's
    no invalid case the way a color value has one. None if nothing's
    being renamed.
    """
    global _naming_slot, _name_input
    if _naming_slot is None:
        return None
    slot = _naming_slot
    new_name = _name_input.strip()
    _naming_slot = None
    _name_input = ""
    return slot, new_name


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

    inner_w = max(w - 2, 0)
    text_color = theme.get("text", 0)
    selected_color = theme.get("selected", 0)
    urgent_color = theme.get("urgent", 0)

    for i, slot in enumerate(range(1, SLOT_COUNT + 1)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        has_file = _session_path(slot).exists()
        dot = "●" if has_file else "○"
        dot_color = theme.get("accent", 0) if has_file else (text_color | curses.A_DIM)
        try:
            stdscr.addstr(row, x + 1, dot, dot_color)
        except curses.error:
            pass

        if slot == _naming_slot:
            try:
                stdscr.addstr(row, x + 3, f"{_name_input}_"[:max(inner_w - 2, 0)], selected_color)
            except curses.error:
                pass
            continue

        is_this_expanded = slot == _expanded_slot
        is_row_selected = f"sessions:row:{slot}" == ctx.selected_id
        name = _slot_name(ctx.config, slot)
        # selected only applies to the name while browsing (level 1) —
        # once expanded the cursor has moved onto one of the actions,
        # so the name itself goes back to plain text color.
        name_color = selected_color if (is_row_selected and not is_this_expanded) else text_color
        try:
            stdscr.addstr(row, x + 3, name[:max(NAME_COL_WIDTH - 1, 0)], name_color)
        except curses.error:
            pass

        # Every row always shows its 4 actions, not just the expanded
        # one — dimmed everywhere except the currently-expanded row,
        # where they're plain (or selected-colored, cursor permitting)
        # and actually navigable. Right-aligned to the box's own right
        # edge (see _action_positions), not tied to name column width.
        # DEL always uses the urgent role (same as power_menu's own
        # confirm=true actions) — it's the one destructive action here,
        # worth standing out even dimmed, not just once selected.
        for action, cx in _action_positions(x, w):
            label = action.upper()
            base_color = urgent_color if action == "del" else text_color
            if is_this_expanded:
                is_selected = f"sessions:action:{slot}:{action}" == ctx.selected_id
                color = selected_color if is_selected else base_color
            else:
                color = base_color | curses.A_DIM
            try:
                stdscr.addstr(row, cx, label, color)
            except curses.error:
                pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box

    if _expanded_slot is not None:
        row = y + 1 + (_expanded_slot - 1)
        items = []
        for action, cx in _action_positions(x, w):
            items.append(NavItem(
                id=f"sessions:action:{_expanded_slot}:{action}",
                rect=(cx, row, len(action), 1),
                focus_target=f"{_expanded_slot}:{action}",
                target_kind="session_action",
            ))
        return items

    items = []
    for i, slot in enumerate(range(1, SLOT_COUNT + 1)):
        row = y + 1 + i
        items.append(NavItem(
            id=f"sessions:row:{slot}",
            rect=(x + 1, row, w - 2, 1),
            focus_target=str(slot),
            target_kind="session_row",
        ))
    return items


def handle_row(ctx, item, cfg):
    global _expanded_slot
    _expanded_slot = int(item.focus_target)
    # See ActionContext.reselect_item_id's docstring — without this,
    # the just-selected "sessions:row:N" id vanishes from nav_items()'
    # own output the moment _expanded_slot changes (it now reports
    # this slot's 4 actions instead), which tripped main.py's "no
    # longer valid, recover somehow" fallback into jumping to whatever
    # the sidebar shows — found live, not what expanding a row should do.
    ctx.reselect_item_id = f"sessions:action:{_expanded_slot}:{ACTIONS[0]}"
    return False, None


def handle_action(ctx, item, cfg):
    global _expanded_slot
    slot_str, action = item.focus_target.split(":", 1)
    slot = int(slot_str)
    path = _session_path(slot)

    if action == "save":
        state = ctx.provider.get_state()
        entries = capture_session(state, ctx.provider)
        save_session(entries, path)
        _expanded_slot = None
        # Same reasoning as handle_row/collapse() — nav_items() drops
        # this action id the instant _expanded_slot resets, which would
        # otherwise trip the sidebar-jump recovery bug found live.
        # Unlike load below, save has no reason to jump to the sidebar
        # (nothing new appeared to look at) — just back to browsing,
        # cursor on the row just saved.
        ctx.reselect_item_id = f"sessions:row:{slot}"
        return False, None

    if action == "load":
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
        _expanded_slot = None
        return False, None

    if action == "del":
        if not path.exists():
            return False, None
        return False, {
            "command": f"rm {shlex.quote(str(path))}",
            "shell_true": False,
            "confirm_text": f"Delete session {slot}?",
            "dismiss_after_confirm": False,
            "module": "sessions",
        }

    if action == "name":
        start_naming(slot, _slot_name(cfg, slot))
        return False, None

    return False, None


HANDLERS = {
    "session_row": handle_row,
    "session_action": handle_action,
}
