"""System monitor module (VISION.md's R6): a scrollable per-window
CPU/RAM list on top (fixed VISIBLE_SLOTS-visible, same windowed_list.py
mechanic media.py's Now Playing/Output sections already use), a
compact multi-column system-stats block in the middle (CPU/RAM/disk/
load/temp/hottest-sensor/throttle/swap — columns side by side, not one
value per line, since the box's own row budget can't fit that), and a
one-line diagnostics summary at the bottom (failed units + OOM +
deduped general errors — hover shows the full breakdown via preview.py,
same NavItem.preview_text mechanism every other module's hover-preview
already uses).

Window rows use the exact two-level browsing/expanded model sessions.py
established (see that module's own docstring for the full reasoning):
level 1 is one row per window with CLOSE/KILL/NICE dimmed at the box's
right edge; Enter "expands" the row, lighting those three actions up
and moving the cursor onto them (_action_positions here is sessions.py's
own _action_positions, same right-aligned layout, 3 actions instead of
4). NICE additionally opens its own small inline numeric input
("-3_", same in-place-inline-text idiom sessions.py's rename field
uses) rather than running immediately — is_editing_nice()/
start_nice_edit()/handle_nice_key()/apply_nice_edit() are the hooks
main.py's own input_claim dispatch calls into for that, mirroring
sessions.py's is_naming()/start_naming()/handle_naming_key()/
apply_naming() quartet shape exactly.

NICE only ever WRITES 0..19 (positive/"nicer" only) — lowering
niceness (negative values) needs CAP_SYS_NICE/root tuicc doesn't
assume it has, and os.setpriority() would just fail depending on the
kernel; restricting the input range to what's always safe sidesteps
that failure mode entirely rather than handling it after the fact.
CLOSE/KILL both act through pieces that already exist elsewhere:
Provider.close_window() (already implemented on both sway.py/i3.py, no
provider changes needed) and a plain pending_confirm dict routed
through actions.py's existing spawn_detached(shell_true=False) —
KILL needed zero changes to actions.py itself, `kill -9 <pid>` already
splits cleanly via shlex.split, exactly like sessions.py's own "del"
action.

_expanded_window_id/_nice_target/_nice_input are plain module-level
state — same "private, nothing else needs to reach into" pattern
sessions.py's _expanded_slot/_naming_slot/_name_input and media.py's
_expanded_bus_name already use.
"""

import curses
import os

from tuicc.keybinds import key_label
from tuicc.modules import launcher as launcher_mode
from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines
from tuicc.sensors import describe_sensor
from tuicc.windowed_list import VISIBLE_SLOTS, header_with_count, section_nav_indices, section_rows, window_start

WINDOW_ACTIONS = ["close", "kill", "nice"]

_expanded_window_id = None  # str | None — which window's CLOSE/KILL/NICE actions are showing, if any
_nice_target = None         # {"window_id", "pid", "current"} | None — set while NICE's own input is open
_nice_input = ""


# ---------- expand/collapse (level 1 <-> level 2) ----------

def is_expanded() -> bool:
    return _expanded_window_id is not None


def collapse() -> str | None:
    """Mirrors sessions.py's collapse() exactly — returns the window_id
    that WAS expanded (None if none was) so the caller can reselect
    "sysmon:<window_id>:row" directly; nav_items() stops reporting the
    just-selected action id the instant this collapses, which would
    otherwise trip main.py's stale-selection recovery into jumping to
    the sidebar (see sessions.py's own collapse() docstring for the
    full live-found reasoning).
    """
    global _expanded_window_id
    window_id = _expanded_window_id
    _expanded_window_id = None
    return window_id


def _reconcile_expanded_state(windows: list) -> None:
    """Clears _expanded_window_id if that window has closed/vanished
    from the current snapshot — same reasoning media.py's own
    _reconcile_expanded_state gives for players that quit.
    """
    global _expanded_window_id
    if _expanded_window_id is None:
        return
    if not any(w.window_id == _expanded_window_id for w in windows):
        _expanded_window_id = None


# ---------- NICE input (a 4th input_claim consumer, see main.py) ----------

def is_editing_nice() -> bool:
    return _nice_target is not None


def start_nice_edit(window_id: str, pid: int, current: int | None) -> None:
    global _nice_target, _nice_input
    _nice_target = {"window_id": window_id, "pid": pid, "current": current}
    _nice_input = ""


def handle_nice_key(key: int) -> bool:
    """Same still_claiming shape as sessions.py's handle_naming_key —
    False only on Escape. Accepts digits only (see this module's own
    docstring for why NICE is positive-only 0..19, no sign character
    needed at all as a result).
    """
    global _nice_target, _nice_input
    if key == 27:  # Escape
        _nice_target = None
        _nice_input = ""
        return False
    if key in (curses.KEY_BACKSPACE, 127, 8):
        _nice_input = _nice_input[:-1]
        return True
    if 48 <= key <= 57:  # '0'-'9'
        _nice_input += chr(key)
    return True


def apply_nice_edit() -> tuple[str, int] | None:
    """(window_id, applied_value) on success. Applies immediately via
    os.setpriority — this IS the confirm action itself, no further
    pending_confirm dialog on top (NICE was explicitly agreed safe
    enough not to need one, unlike KILL). None (edit stays open, same
    "invalid keeps editing open" idea help_mode's own color editor
    uses) when the typed value is empty, unparseable, or outside
    0..19 — a permission failure from setpriority itself (a pid this
    process doesn't own) is deliberately NOT caught here, it propagates
    to whatever main.py's own exception handling does with an
    unexpected error, same no-silent-failure stance as everywhere else
    (this is a REAL failure, unlike "value out of range", which is
    just user input to correct, not an error).
    """
    global _nice_target, _nice_input
    if _nice_target is None:
        return None
    try:
        value = int(_nice_input)
    except ValueError:
        return None
    if not (0 <= value <= 19):
        return None
    window_id = _nice_target["window_id"]
    pid = _nice_target["pid"]
    os.setpriority(os.PRIO_PROCESS, pid, value)
    _nice_target = None
    _nice_input = ""
    return window_id, value


# ---------- selection / windowing ----------

def _selected_window_index(windows: list, selected_id: str | None, expanded_window_id: str | None) -> int | None:
    """Mirrors media.py's _selected_player_index — checks
    expanded_window_id FIRST (an expanded row's own action ids still
    embed the same window_id, but checking the expansion directly is
    more direct than reparsing one of the three action-id shapes).
    """
    target = expanded_window_id
    if target is None and selected_id and selected_id.startswith("sysmon:") and selected_id.endswith(":row"):
        target = selected_id.split(":")[1]
    if target is None:
        return None
    for i, w in enumerate(windows):
        if w.window_id == target:
            return i
    return None


def visible_window_ids(windows: list, selected_id: str | None, visible_slots: int = VISIBLE_SLOTS) -> set:
    """Which window_ids fall within the CURRENT VISIBLE_SLOTS scroll
    window — main.py calls this, main-thread, right after building this
    frame's flattened window list and BEFORE handing pids off to the
    background "windows" Domain, to decide which windows are worth an
    on-demand Provider.resolve_pid() call at all (i3 has no native pid
    on its own IPC tree — see providers/base.py's resolve_pid()
    docstring, and procmon.py's own module docstring for why this has
    to stay main-thread-only). Not resolving every window's pid on
    every frame regardless of visibility matters because resolve_pid()
    is a real, possibly-slow on-demand X11 lookup on i3.
    """
    selected_index = _selected_window_index(windows, selected_id, _expanded_window_id)
    start = window_start(len(windows), selected_index, visible_slots)
    return {w.window_id for w in windows[start:start + visible_slots]}


def _window_action_positions(x: int, w: int) -> list[tuple[str, int]]:
    """Sessions.py's own _action_positions, same right-aligned layout,
    3 actions instead of 4 — shared by draw() and nav_items() so a
    row's highlighted/selectable regions always match what's drawn.
    """
    labels = [action.upper() for action in WINDOW_ACTIONS]
    total_width = sum(len(label) for label in labels) + (len(labels) - 1)
    cx = x + w - 1 - total_width
    positions = []
    for action, label in zip(WINDOW_ACTIONS, labels):
        positions.append((action, cx))
        cx += len(label) + 1
    return positions


def _pid_for_window(ctx, window_id: str) -> int | None:
    windows = ctx.status.get("windows") if ctx.status is not None else None
    for w in (windows or []):
        if w.window_id == window_id:
            return w.pid
    return None


def _current_nice(pid: int) -> int | None:
    """None if this process can't read the target's priority at all
    (already exited, or a permission boundary) — NICE's own input
    still opens either way, just without a "current: N" hint.
    """
    try:
        return os.getpriority(os.PRIO_PROCESS, pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _friendly_app_name(app_id: str | None) -> str:
    """The app's own display name ("Firefox", "Visual Studio Code"),
    not the raw app_id ("firefox", "code") a window row would otherwise
    show — found live, asked for: the raw window TITLE ("settings.json
    - tuicc - Visual Studio Code") is what this module showed at
    first, and it read as noise for a resource-monitor row, where
    "which APP is this" matters far more than "which specific document/
    tab". Reuses launcher.py's own scan_desktop_apps() cache (matching
    a window's app_id against whichever .desktop entry's own
    StartupWMClass=/filename hint equals it — case-insensitively here,
    looser than pending_moves.py's own app_id tier, which needs an
    EXACT match to disambiguate between multiple pending spawns of
    possibly-identical app_ids; a display-name lookup has no such
    collision risk, so the looser match is fine) rather than a second,
    hardcoded app_id->name table, which CONTRIBUTING.md's "no hardcoded
    personal preferences" rule would rule out anyway (there's no
    generic way to know every app_id's own preferred display name in
    advance). Falls
    back to the raw app_id itself when no .desktop entry matches — a
    window from an app with no .desktop file at all (a dev build, a
    custom script) is a real, expected case, not a bug to special-case
    around.
    """
    if not app_id:
        return "?"
    app_l = app_id.lower()
    for name, _exec_cmd, app_id_hint in launcher_mode.get_apps():
        if app_id_hint.lower() == app_l:
            return name
    return app_id


def _format_window_label(win_stat, available_w: int) -> str:
    """"[13% 24M] Visual Studio Code…" — the CPU/RAM readout ALWAYS
    shows in full (fixed-width, never truncated away) since it's the
    actual point of a system-monitor row; the friendly app name (see
    _friendly_app_name) gets whatever width is left over, truncated
    with a trailing "…" when it doesn't fit. Found live: showing the
    full window title first meant a long title (VS Code/Firefox tab
    titles routinely run 40+ characters) silently pushed the CPU/RAM
    numbers themselves off the edge of the box entirely — the least
    useful part winning the space over the most useful part.
    """
    cpu_str = f"{win_stat.cpu_percent:.0f}%" if win_stat.cpu_percent is not None else "?%"
    ram_str = f"{win_stat.rss_kb / 1024:.0f}M" if win_stat.rss_kb is not None else "?M"
    stats_str = f"[{cpu_str} {ram_str}]"
    name = _friendly_app_name(win_stat.app_id)

    name_w = max(available_w - len(stats_str) - 1, 0)  # -1 for the space between stats and name
    if len(name) > name_w:
        name = (name[:max(name_w - 1, 0)] + "…") if name_w > 0 else ""

    return f"{stats_str} {name}" if name else stats_str


# ---------- middle "compact stats" section ----------

def _format_stats_lines(sysinfo_data: dict | None, sensors_data: dict | None) -> list[str]:
    """Two fixed text lines — columns side by side, not one value per
    line, agreed with the user specifically because the box's own row
    budget can't fit one-value-per-line (see module docstring). Any
    single value that's currently unknown (a poll that hasn't completed
    yet, or a real poll error) shows as "?" rather than a misleading 0
    — same None-vs-0 discipline as everywhere else in this codebase.
    """
    def _pct(value):
        return f"{value:.0f}%" if value is not None else "?%"

    def _temp(value):
        return f"{value:.0f}°C" if value is not None else "?°C"

    cpu = sysinfo_data.get("cpu_percent") if sysinfo_data else None
    ram = sysinfo_data.get("ram_percent") if sysinfo_data else None
    disk = sysinfo_data.get("disk") if sysinfo_data else None
    disk_pct = disk.get("percent") if disk else None
    load = sysinfo_data.get("load_average") if sysinfo_data else None
    load_str = f"{load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f}" if load else "?/?/?"

    line1 = f"CPU {_pct(cpu)}  RAM {_pct(ram)}  DISK {_pct(disk_pct)}  LOAD {load_str}"

    cpu_temp_entry = sensors_data.get("cpu_temp") if sensors_data else None
    hottest_entry = sensors_data.get("hottest") if sensors_data else None
    cpu_temp_str = _temp(cpu_temp_entry[0]) if cpu_temp_entry else "?°C"
    if hottest_entry:
        hot_value, hot_chip, hot_feature = hottest_entry
        hot_str = f"{_temp(hot_value)} ({describe_sensor(hot_chip, hot_feature)})"
    else:
        hot_str = "?°C"

    swap_in = sysinfo_data.get("swap_in_kb_s") if sysinfo_data else None
    swap_out = sysinfo_data.get("swap_out_kb_s") if sysinfo_data else None
    swap_str = f"{swap_in:.0f}/{swap_out:.0f} KB/s" if swap_in is not None and swap_out is not None else "?/? KB/s"

    throttle_flag = "  THROTTLED" if (sysinfo_data and sysinfo_data.get("throttled_recently")) else ""

    line2 = f"TEMP {cpu_temp_str}  HOT {hot_str}  SWAP {swap_str}{throttle_flag}"

    return [line1, line2]


# ---------- bottom diagnostics line ----------

def _diagnostics_summary_text(diag: dict | None) -> str:
    if diag is None:
        return "Diagnostics: checking..."
    return f"Diagnostics: {diag['summary']}"


def _diagnostics_preview_text(diag: dict | None, theme) -> list[tuple[str, int]] | None:
    """The hover-preview breakdown shown in preview.py when the
    diagnostics summary row is selected (see NavItem.preview_text's own
    docstring for the mechanism this reuses, unchanged, from every
    other module's hover-preview). None only when the poll hasn't
    completed at all yet — draw()'s own summary text already covers
    that case ("checking...").
    """
    if diag is None:
        return None
    lines = []
    for unit in (diag.get("failed_units") or []):
        lines.append((f"✗ {unit}", theme.get("urgent", 0)))
    for event in (diag.get("oom_events") or []):
        text = f"OOM: {event['message']}"
        if event["count"] > 1:
            text += f" (x{event['count']})"
        lines.append((text[:100], theme.get("urgent", 0)))
    for entry in (diag.get("general_errors") or []):
        text = f"{entry['identifier']}: {entry['message']}"
        if entry["count"] > 1:
            text += f" (x{entry['count']})"
        lines.append((text[:100], theme.get("text", 0)))
    if not lines:
        lines = [("No issues found", theme.get("text", 0))]
    return lines


# ---------- row building (shared by draw()/nav_items(), see media.py's own reasoning) ----------

def _build_rows(ctx, box_h):
    windows = ctx.status.get("windows") if ctx.status is not None else None
    windows_error = ctx.status.get_error("windows") if ctx.status is not None else None
    sysinfo_data = ctx.status.get("sysinfo") if ctx.status is not None else None
    sensors_data = ctx.status.get("sensors") if ctx.status is not None else None
    diagnostics_data = ctx.status.get("diagnostics") if ctx.status is not None else None

    _reconcile_expanded_state(windows or [])

    selected_index = _selected_window_index(windows or [], ctx.selected_id, _expanded_window_id)

    rows = [("header", header_with_count("Windows", windows))]
    rows.extend(section_rows(windows, windows_error, selected_index, "window", "window"))
    rows.append(("spacer", None))
    for line in _format_stats_lines(sysinfo_data, sensors_data):
        rows.append(("stats_line", line))
    rows.append(("diagnostics", diagnostics_data))
    return rows


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="System")

    if ctx.pending_confirm is not None and ctx.pending_confirm.get("module") == module_name:
        confirm_text = ctx.pending_confirm.get("confirm_text")
        hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
        lines = []
        if confirm_text:
            lines.append((confirm_text, theme.get("urgent", 0)))
        lines.append((hint, theme.get("text", 0)))
        draw_centered_lines(stdscr, box, lines)
        return

    inner_w = max(w - 4, 0)

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "header":
            try:
                stdscr.addstr(row, x + 2, payload[:max(inner_w, 0)], theme.get("accent", 0) | curses.A_BOLD)
            except curses.error:
                pass

        elif kind == "error":
            try:
                stdscr.addstr(row, x + 2, f"⚠ {payload}"[:max(inner_w, 0)], theme.get("urgent", 0))
            except curses.error:
                pass

        elif kind == "empty_slot":
            try:
                stdscr.addstr(row, x + 2, payload[:max(inner_w, 0)], theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

        elif kind == "spacer":
            pass

        elif kind == "stats_line":
            try:
                stdscr.addstr(row, x + 2, payload[:max(inner_w, 0)], theme.get("text", 0))
            except curses.error:
                pass

        elif kind == "diagnostics":
            diag = payload
            has_issues = diag is not None and diag["summary"] not in ("All clear", "Diagnostics unavailable")
            is_row_selected = "sysmon:diagnostics" == ctx.selected_id
            color = theme.get("urgent", 0) if has_issues else theme.get("text", 0)
            if is_row_selected:
                color = theme.get("selected", 0)
            try:
                stdscr.addstr(row, x + 2, _diagnostics_summary_text(diag)[:max(inner_w, 0)], color)
            except curses.error:
                pass

        elif kind == "window":
            win_stat = payload
            is_this_expanded = win_stat.window_id == _expanded_window_id
            something_expanded = _expanded_window_id is not None

            is_row_selected = f"sysmon:{win_stat.window_id}:row" == ctx.selected_id
            if something_expanded:
                text_color, attr = theme.get("text", 0), curses.A_DIM
            elif is_row_selected:
                text_color, attr = theme.get("selected", 0), curses.A_BOLD
            else:
                text_color, attr = theme.get("text", 0), 0

            positions = _window_action_positions(x, w)
            actions_start_x = positions[0][1] if positions else (x + w - 1)
            available_w = max(actions_start_x - 1 - (x + 2), 0)
            label = _format_window_label(win_stat, available_w)

            try:
                stdscr.addstr(row, x + 2, label[:available_w], text_color | attr)
            except curses.error:
                pass

            for action, cx in positions:
                base_color = theme.get("urgent", 0) if action in ("close", "kill") else theme.get("text", 0)
                if is_this_expanded:
                    if action == "nice" and is_editing_nice() and _nice_target["window_id"] == win_stat.window_id:
                        text = f"{_nice_input}_"
                    else:
                        text = action.upper()
                    is_selected = f"sysmon:{win_stat.window_id}:{action}" == ctx.selected_id
                    color = theme.get("selected", 0) if is_selected else base_color
                else:
                    text = action.upper()
                    color = base_color | curses.A_DIM
                try:
                    stdscr.addstr(row, cx, text, color)
                except curses.error:
                    pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    windows = (ctx.status.get("windows") if ctx.status is not None else None) or []
    theme = ctx.theme or {}

    items: list[NavItem] = []
    window_rows: list[int] = []

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "window":
            win_stat = payload
            if win_stat.window_id == _expanded_window_id:
                for action, cx in _window_action_positions(x, w):
                    items.append(NavItem(
                        id=f"sysmon:{win_stat.window_id}:{action}",
                        rect=(cx, row, len(action), 1),
                        focus_target=f"{win_stat.window_id}:{action}",
                        target_kind="sysmon_action",
                    ))
            else:
                items.append(NavItem(
                    id=f"sysmon:{win_stat.window_id}:row",
                    rect=(x + 1, row, w - 2, 1),
                    focus_target=win_stat.window_id,
                    target_kind="sysmon_row",
                ))
            window_rows.append(row)

        elif kind == "diagnostics":
            diag = payload
            items.append(NavItem(
                id="sysmon:diagnostics",
                rect=(x + 1, row, w - 2, 1),
                focus_target=None,
                target_kind="sysmon_diagnostics",
                preview_text=_diagnostics_preview_text(diag, theme),
            ))

    # peek items for the scrollable window section, same mechanism
    # media.py's own nav_items() uses for Now Playing/Output.
    selected_index = _selected_window_index(windows, ctx.selected_id, _expanded_window_id)
    before_i, after_i = section_nav_indices(len(windows), selected_index)
    window_items = [it for it in items if it.target_kind in ("sysmon_row", "sysmon_action")]
    other_items = [it for it in items if it.target_kind not in ("sysmon_row", "sysmon_action")]
    if before_i is not None and window_rows:
        window_items = [NavItem(
            id=f"sysmon:{windows[before_i].window_id}:row",
            rect=(x + 1, window_rows[0], w - 2, 1),
            focus_target=windows[before_i].window_id, target_kind="sysmon_row",
        )] + window_items
    if after_i is not None and window_rows:
        window_items = window_items + [NavItem(
            id=f"sysmon:{windows[after_i].window_id}:row",
            rect=(x + 1, window_rows[-1], w - 2, 1),
            focus_target=windows[after_i].window_id, target_kind="sysmon_row",
        )]

    return window_items + other_items


def handle_row(ctx, item, cfg):
    """Enter on a browsing-level window row expands it — mirrors
    sessions.py's handle_row exactly, including the reselect_item_id
    fix-up (see ActionContext's own docstring).
    """
    global _expanded_window_id
    _expanded_window_id = item.focus_target  # window_id
    ctx.reselect_item_id = f"sysmon:{item.focus_target}:close"
    return False, None


def handle_action(ctx, item, cfg):
    global _expanded_window_id
    window_id, action = item.focus_target.split(":", 1)

    if action == "close":
        ctx.provider.close_window(window_id)
        _expanded_window_id = None
        ctx.reselect_item_id = None
        return False, None

    if action == "kill":
        pid = _pid_for_window(ctx, window_id)
        if pid is None:
            return False, None
        return False, {
            "command": f"kill -9 {pid}",
            "shell_true": False,
            "confirm_text": f"Kill pid {pid}?",
            "dismiss_after_confirm": False,
            "module": "sysmon",
        }

    if action == "nice":
        pid = _pid_for_window(ctx, window_id)
        if pid is None:
            return False, None
        start_nice_edit(window_id, pid, _current_nice(pid))
        return False, None

    return False, None


HANDLERS = {
    "sysmon_row": handle_row,
    "sysmon_action": handle_action,
}
