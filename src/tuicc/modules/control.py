"""Control module: user-defined [[control.toggle]] entries as a dense
toggle grid — VISION.md's R5. "Toggles are a contract, not features":
this module has zero per-toggle code, it just renders whatever
cfg.control_toggles (config.py's _build_control_toggles) says and
advances through it on Enter. A binary on/off switch and a multi-way
cycle (Performance Mode's power-saver/balanced/performance) are the
same row here — see control.py's own module docstring for why that
unification happened.

---
IMPORTANT: Each module owns both how it draws itself and where its own
focusable items are — the core never guesses a module's internal
layout.
"""

import curses
import time

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline
from tuicc import control


def _state_index(states: list[dict], current_name: str | None) -> int | None:
    if current_name is None:
        return None
    for i, state in enumerate(states):
        if state["name"] == current_name:
            return i
    return None


def _state_by_name(states: list[dict], name: str) -> dict:
    for state in states:
        if state["name"] == name:
            return state
    raise ValueError(f"no state named {name!r} in {[s['name'] for s in states]}")


def _toggle_dot_color(ctx, toggle_index: int, state_index: int | None, theme: dict) -> int:
    """ctx.control_colors (main.py's assign_control_toggle_pairs, see
    its own docstring) holds a curses color pair only for states that
    configured an explicit `color` — everything else (state_index is
    None, e.g. current state unknown/errored, or that particular state
    just didn't set one) falls back to the theme's own "accent" role,
    same "look it up, fall back if missing" pattern connectivity.py's
    _connection_dot already uses for connected/disconnected.
    """
    if state_index is None:
        return theme.get("accent", 0)
    return ctx.control_colors.get((toggle_index, state_index), theme.get("accent", 0))


def _row_kind(current_name: str | None, error: str | None, action_error: str | None, pending: bool) -> str:
    """Which of the four mutually-exclusive ways a toggle's row can
    render, decided once so draw() doesn't repeat this priority logic
    inline (and so it's testable without a real curses screen — see
    connectivity.py's own _build_rows for the established pattern this
    follows).

    Priority: pending > action_error > poll_error > normal.
    action_error outranks a plain poll error — found live building
    this module: the last thing the user DID (pressed Enter, the
    command failed) is more relevant than the last thing that was
    merely observed (a status_command poll failing independently).
    """
    if pending:
        return "pending"
    if action_error:
        return "action_error"
    if current_name is None and error:
        return "poll_error"
    return "normal"


def _pending_blink_style(theme):
    """Blinks between selected and dim twice a second — same idiom as
    connectivity.py's own _pending_blink_style (duplicated rather than
    imported: each module stays self-contained, matching this
    codebase's existing precedent of connectivity.py not importing it
    from anywhere else either).
    """
    blink_on = int(time.time() * 2) % 2 == 0
    if blink_on:
        return theme.get("selected", 0), curses.A_BOLD
    return theme.get("text", 0), curses.A_DIM


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}
    toggles = ctx.config.control_toggles

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Control")

    if not toggles:
        try:
            stdscr.addstr(y + 1, x + 2, "(no toggles configured)", theme.get("text", 0) | curses.A_DIM)
        except curses.error:
            pass
        return

    inner_w = max(w - 4, 0)
    for i, toggle in enumerate(toggles):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        is_selected = f"control:{i}" == ctx.selected_id
        current_name = ctx.status.get(f"toggle:{i}") if ctx.status is not None else None
        error = ctx.status.get_error(f"toggle:{i}") if ctx.status is not None else None
        action_error = ctx.status.get_action_error(f"toggle:{i}") if ctx.status is not None else None
        pending = ctx.status is not None and ctx.status.is_pending(f"toggle:{i}", i)

        kind = _row_kind(current_name, error, action_error, pending)

        if kind == "pending":
            text_color, attr = _pending_blink_style(theme)
            dot, dot_color = "●", (text_color | attr)
        elif kind == "action_error":
            # Found live: a toggle's command failing (e.g. gammastep
            # exiting immediately for lack of GeoClue2) used to produce
            # zero visible feedback — the row just silently kept
            # showing its old (correct, but uninformative) state.
            dot, dot_color = "⚠", theme.get("urgent", 0)
            text_color, attr = theme.get("urgent", 0), curses.A_BOLD
        elif kind == "poll_error":
            # No-silent-failure (VISION.md R3/R5): distinct from
            # "unknown, not polled yet" — same "error" row kind as
            # connectivity.py's _build_rows.
            dot, dot_color = "⚠", theme.get("urgent", 0)
            text_color, attr = theme.get("urgent", 0), 0
        else:
            state_index = _state_index(toggle["states"], current_name)
            dot = "●" if current_name is not None else "○"
            dot_color = _toggle_dot_color(ctx, i, state_index, theme)
            text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
            attr = curses.A_BOLD if is_selected else 0

        label = toggle["label"]
        if kind == "action_error":
            suffix = " [ERROR]"
        elif kind == "poll_error":
            suffix = f" ({error})"
        elif current_name is not None:
            suffix = f" [{current_name}]"
        else:
            suffix = ""
        rest = f" {label}{suffix}"[:max(inner_w - 1, 0)]
        try:
            stdscr.addstr(row, x + 2, dot, dot_color)
            stdscr.addstr(row, x + 3, rest, text_color | attr)
        except curses.error:
            pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    toggles = ctx.config.control_toggles
    theme = ctx.theme or {}

    items = []
    for i, toggle in enumerate(toggles):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        current_name = ctx.status.get(f"toggle:{i}") if ctx.status is not None else None
        action_error = ctx.status.get_action_error(f"toggle:{i}") if ctx.status is not None else None
        next_name = control.next_state_name(toggle["states"], current_name)
        next_command = _state_by_name(toggle["states"], next_name)["command"]
        preview_text = [
            (f"> {toggle['label']}: {current_name or '?'} → {next_name} <", theme.get("accent", 0)),
            (f"$ {next_command}", theme.get("text", 0)),
        ]
        if toggle["shell_true"]:
            preview_text.append(("!! SHELL=TRUE !!", theme.get("urgent", 0)))
        if action_error:
            # The failed command's own captured output (see
            # control._run_detached_detecting_quick_failure) — found
            # live, this used to be invisible entirely. One
            # preview_text ENTRY per physical line, not one entry
            # holding an embedded "\n" — draw_centered_lines
            # (render_utils.py) positions each entry as exactly one
            # row via its own x/y math; a raw "\n" inside a single
            # entry's text just makes curses jump to column 0 of the
            # next real terminal row, escaping the box entirely —
            # found live, a multi-line command like gammastep's (3
            # separate "Error: ..." lines) rendered the first line
            # positioned correctly and the rest floating unpositioned
            # elsewhere on screen.
            for line in action_error.splitlines():
                preview_text.append((f"⚠ {line}", theme.get("urgent", 0)))

        items.append(NavItem(
            id=f"control:{i}",
            rect=(x + 1, row, w - 2, 1),
            focus_target=toggle["label"],
            target_kind="control_toggle",
            preview_text=preview_text,
        ))
    return items


TARGET_KIND = "control_toggle"


def handle(ctx, item, cfg):
    """Enter always advances to the next configured state — a plain
    toggle and a multi-way cycle are the same action here, see this
    module's own docstring. pending_key=toggle_index (not target_name):
    only one advance can be meaningfully in flight per toggle at a
    time, matching how audio's own set_volume uses pending_key for the
    same reason (see status_worker.py's request_action docstring).
    """
    toggle_index = int(item.id.split(":")[1])
    toggle = cfg.control_toggles[toggle_index]
    current_name = ctx.status.get(f"toggle:{toggle_index}")
    target_name = control.next_state_name(toggle["states"], current_name)
    ctx.status.request_action(f"toggle:{toggle_index}", "advance", target_name, pending_key=toggle_index)
    return False, None
