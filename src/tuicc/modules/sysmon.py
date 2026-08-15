"""System monitor module (CLAUDE/VISION.md's R6): a scrollable per-window
CPU/RAM list on top (fixed VISIBLE_SLOTS window, same windowed_list.py
mechanic media.py's sections use), a compact multi-column system-stats
block in the middle (CPU/RAM/disk/load/temp/hottest-sensor/throttle/
swap, columns side by side since the row budget can't fit one value per
line), and a one-line diagnostics summary at the bottom (failed units +
OOM + deduped general errors; hover shows the full breakdown via
preview.py).

Window rows use the same two-level browsing/expanded row model as
sessions.py (CLOSE/KILL/NICE), and NICE opens its own inline numeric
input like sessions.py's rename field. See
CLAUDE/NOTES/design-decisions.md#sysmon-module-design for the full
reasoning, NICE's write-range restriction, and how CLOSE/KILL reuse
existing pieces rather than adding new ones.
"""

import curses
import os

from tuicc.keybinds import key_label
from tuicc.modules import launcher as launcher_mode
from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines, display_width, wc_truncate
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


# ---------- NICE input (main.py's mode_stack "sysmon_nice" tier) ----------

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
    os.setpriority — no confirm dialog on top (NICE is safe enough not
    to need one, unlike KILL). None (edit stays open) when the typed
    value is empty, unparseable, or outside 0..19. A real setpriority
    permission failure is deliberately not caught here — it propagates,
    same no-silent-failure stance as elsewhere, unlike "out of range"
    which is just correctable user input.
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


def _resource_drain_key(cpu_percent: float | None, rss_kb: int | None):
    """CPU% primary (the live, right-now-draining signal), RSS as a
    tiebreak — same convention top/htop's own default CPU-sort uses.
    An unknown value sorts LAST within its tier, not first: "unknown"
    isn't "draining a lot", it's "we don't know yet" — same None-vs-0
    discipline as everywhere else in this codebase.
    """
    return (cpu_percent is None, -(cpu_percent or 0), rss_kb is None, -(rss_kb or 0))


def sort_windows_by_drain(windows: list, known_stats: list | None = None) -> list:
    """Most resource-intensive window first. `windows` items normally
    carry their own .cpu_percent/.rss_kb (procmon.WindowStat) unless
    `known_stats` is given (matched by .window_id) — main.py needs that
    form since its own per-frame window list has no cpu/rss until the
    background "windows" Domain's next poll runs, so it sorts by the
    most recently known sample instead, keeping visible_window_ids()
    in the same order draw()/nav_items() show.
    """
    lookup = {s.window_id: s for s in known_stats} if known_stats is not None else None

    def key(w):
        if lookup is not None:
            stat = lookup.get(w.window_id)
            cpu, rss = (stat.cpu_percent, stat.rss_kb) if stat is not None else (None, None)
        else:
            cpu, rss = w.cpu_percent, w.rss_kb
        return _resource_drain_key(cpu, rss)

    return sorted(windows, key=key)


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
    """The app's own display name ("Firefox"), not the raw app_id
    ("firefox") or the window title. See
    CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why, and how
    it's resolved via launcher.py's scan_desktop_apps() cache rather
    than a hardcoded table. Falls back to the raw app_id when no
    .desktop entry matches — a window from an app with no .desktop file
    (a dev build, a custom script) is a real, expected case.
    """
    if not app_id:
        return "?"
    app_l = app_id.lower()
    for name, _exec_cmd, app_id_hint in launcher_mode.get_apps():
        if app_id_hint.lower() == app_l:
            return name
    return app_id


def _format_window_label(win_stat, available_w: int) -> str:
    """"[13% 24M] Visual Studio Code…" — the CPU/RAM readout always
    shows in full; the friendly app name (see _friendly_app_name) gets
    whatever width is left over, truncated with a trailing "…". See
    CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why the
    numbers take priority over the name.
    """
    cpu_str = f"{win_stat.cpu_percent:.0f}%" if win_stat.cpu_percent is not None else "?%"
    ram_str = f"{win_stat.rss_kb / 1024:.0f}M" if win_stat.rss_kb is not None else "?M"
    stats_str = f"[{cpu_str} {ram_str}]"
    # app_id-derived, typically ASCII in practice, but not guaranteed —
    # a misbehaving app can set anything, so this stays width-aware
    # rather than assuming.
    name = _friendly_app_name(win_stat.app_id)

    name_w = max(available_w - len(stats_str) - 1, 0)  # -1 for the space between stats and name
    if display_width(name) > name_w:
        name = (wc_truncate(name, max(name_w - 1, 0)) + "…") if name_w > 0 else ""

    return f"{stats_str} {name}" if name else stats_str


# ---------- middle "compact stats" section ----------

# Fixed width per grid cell ("LABEL value", left-justified/truncated to
# this) — see CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for
# why a real fixed-column table reads better than a loose run-on line.
GRID_COL_WIDTH = 18


def _grid_row(cells: list[tuple[str, list[tuple[str, str]]]]) -> list[tuple[str, str]]:
    """Joins (label, value_segments) pairs into one row of (text, role)
    segments — role in {"label", "value", "urgent"}, resolved to a
    theme color by draw() (stays pure/curses-free). value_segments lets
    one value carry a differently-colored suffix (HOT's THROTTLED
    marker). Every cell except the last is padded/truncated to
    GRID_COL_WIDTH; the last keeps its natural length — see
    CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why.
    """
    segments: list[tuple[str, str]] = []
    for i, (label, value_segments) in enumerate(cells):
        cell = ([(f"{label:<4} ", "label")] if label else []) + list(value_segments)
        cell_len = sum(len(text) for text, _role in cell)
        is_last = i == len(cells) - 1
        if not is_last:
            if cell_len > GRID_COL_WIDTH:
                trimmed: list[tuple[str, str]] = []
                remaining = GRID_COL_WIDTH
                for text, role in cell:
                    if remaining <= 0:
                        break
                    piece = text[:remaining]
                    trimmed.append((piece, role))
                    remaining -= len(piece)
                cell = trimmed
            elif cell_len < GRID_COL_WIDTH:
                # Padding's own color is irrelevant (it's blank), but
                # needs SOME role to satisfy the (text, role) shape.
                cell = cell + [(" " * (GRID_COL_WIDTH - cell_len), "value")]
        segments.extend(cell)
    # Trailing all-blank segments (the padding on a cell that turned
    # out to be last after all, e.g. a single-cell row) don't need to
    # be drawn — same "rstrip()" idea the old string-based version used.
    while segments and not segments[-1][0].strip():
        segments.pop()
    return segments


def _severity_role(value: float | None, warning: float, urgent: float) -> str:
    """"value" (normal), "warning" (degraded, theme.warning), or
    "urgent" (bad, theme.urgent) — a value at/above `urgent` wins over
    `warning` even if it also clears that lower bar. None (a poll that
    hasn't completed, or a real error) is always "value" — unknown
    isn't "bad", it's just unknown, same None-vs-0 discipline as
    everywhere else in this module.
    """
    if value is None:
        return "value"
    if value >= urgent:
        return "urgent"
    if value >= warning:
        return "warning"
    return "value"


def _pct_text(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "?%"


def _temp_text(value: float | None) -> str:
    return f"{value:.0f}°C" if value is not None else "?°C"


def _compute_metric_cell(metric: str, sysinfo_data: dict | None, sensors_data: dict | None,
                          block: dict) -> tuple[str, list[tuple[str, str]]]:
    """(label, value_segments) for ONE configured [[sysmon.block]]
    entry — turns a metric name into display text + severity role (see
    _severity_role). `block` carries warning/urgent thresholds; None
    for either means never threshold-colored (load/swap's default, see
    DEFAULT_SYSMON_BLOCKS). An unknown value shows "?", not a
    misleading 0.
    """
    label = block.get("label") or metric.upper()
    warning, urgent = block.get("warning"), block.get("urgent")
    thresholded = warning is not None and urgent is not None

    def _role(value):
        return _severity_role(value, warning, urgent) if thresholded else "value"

    if metric == "cpu":
        cpu = sysinfo_data.get("cpu_percent") if sysinfo_data else None
        return label, [(_pct_text(cpu), _role(cpu))]

    if metric == "ram":
        ram = sysinfo_data.get("ram") if sysinfo_data else None
        if not ram:
            return label, [("?/? GiB", "value")]
        gib = 1024 * 1024  # ram's own values are in kB (see sysinfo.get_ram_info)
        # RAM/DISK show used/available amounts, not a bare percent — see
        # CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid. The color
        # still follows the real usage percent even though the
        # displayed text doesn't show one.
        text = f"{ram['used_kb'] / gib:.1f}/{ram['available_kb'] / gib:.1f} GiB"
        return label, [(text, _role(ram["percent"]))]

    if metric == "disk":
        disk = sysinfo_data.get("disk") if sysinfo_data else None
        if not disk or disk.get("used") is None or disk.get("free") is None:
            return label, [("?/? GB", "value")]
        # GB (decimal, /1e9) matching how disk capacity is
        # conventionally reported everywhere else (drive packaging,
        # `df`'s own default) — GiB is RAM's own convention, not this.
        text = f"{disk['used'] / 1e9:.0f}/{disk['free'] / 1e9:.0f} GB"
        return label, [(text, _role(disk.get("percent")))]

    if metric == "load":
        load = sysinfo_data.get("load_average") if sysinfo_data else None
        text = f"{load[0]:.1f}/{load[1]:.1f}/{load[2]:.1f}" if load else "?/?/?"
        return label, [(text, "value")]  # never threshold-colored — see module docstring

    if metric == "cputemp":
        # "CPUTEMP", not just "TEMP" — see
        # CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why,
        # against the HOT block often sitting right below it.
        entry = sensors_data.get("cpu_temp") if sensors_data else None
        value = entry[0] if entry else None
        return label, [(_temp_text(value), _role(value))]

    if metric == "hot":
        entry = sensors_data.get("hottest") if sensors_data else None
        if entry:
            value, chip, feature = entry
            text = f"{_temp_text(value)} ({describe_sensor(chip, feature)})"
            role = _role(value)
        else:
            text, role = "?°C", "value"
        segments = [(text, role)]
        # THROTTLED is a CPU-thermal flag (core_throttle_count deltas,
        # see sysinfo.py) — rides along on HOT's own cell specifically,
        # its own urgent-colored segment. See
        # CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why
        # that placement matters (column-width clipping risk).
        if sysinfo_data and sysinfo_data.get("throttled_recently"):
            segments.append((" THROTTLED", "urgent"))
        return label, segments

    if metric == "swap":
        swap_in = sysinfo_data.get("swap_in_kb_s") if sysinfo_data else None
        swap_out = sysinfo_data.get("swap_out_kb_s") if sysinfo_data else None
        text = (f"{swap_in:.0f}↓/{swap_out:.0f}↑ KB/s"
                if swap_in is not None and swap_out is not None else "?/? KB/s")
        return label, [(text, "value")]  # never threshold-colored — see module docstring

    # config.py's own _build_sysmon_blocks already validates `metric`
    # against SYSMON_METRICS at load time — reaching here would mean
    # that validation and this dispatch drifted out of sync, a real
    # programming error, not a user-facing config mistake.
    raise ValueError(f"No renderer for sysmon metric {metric!r}")


def _format_stats_grid(sysinfo_data: dict | None, sensors_data: dict | None,
                        blocks: list[dict]) -> list[list[tuple[str, str]]]:
    """The System module's compact stats block — one column per
    distinct `block["column"]`, one row per block within it, in
    `block["row"]` order (a sort key, not a literal shared index).
    `blocks` is ctx.config.sysmon_blocks — see CLAUDE/VISION.md's R6
    section for why layout/thresholds are config-driven. Rendered
    row-by-row via _grid_row; a disabled block is skipped, not an
    empty cell.
    """
    enabled_blocks = [b for b in blocks if b.get("enabled", True)]
    columns: dict[int, list[tuple[str, list[tuple[str, str]]]]] = {}
    for block in sorted(enabled_blocks, key=lambda b: (b["column"], b["row"])):
        cell = _compute_metric_cell(block["metric"], sysinfo_data, sensors_data, block)
        columns.setdefault(block["column"], []).append(cell)

    ordered_columns = [columns[c] for c in sorted(columns)]
    if not ordered_columns:
        return []
    max_rows = max(len(col) for col in ordered_columns)
    return [_grid_row([col[i] for col in ordered_columns if i < len(col)]) for i in range(max_rows)]


# ---------- bottom diagnostics line ----------

def _diagnostics_has_issues(diag: dict | None) -> bool:
    """Shared by draw()'s own status dot and nav_items()'s
    NavItem.preview_urgent — one definition of "this is actually a
    problem" (real issues, not "all clear" and not "couldn't check")
    so the two can never silently disagree with each other.
    """
    return diag is not None and diag["summary"] not in ("All clear", "Diagnostics unavailable")


def _diagnostics_summary_text(diag: dict | None) -> str:
    if diag is None:
        return "checking..."
    return diag["summary"]


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

    if windows is not None:
        windows = sort_windows_by_drain(windows)

    _reconcile_expanded_state(windows or [])

    selected_index = _selected_window_index(windows or [], ctx.selected_id, _expanded_window_id)

    rows = [("header", header_with_count("Windows", windows))]
    rows.extend(section_rows(windows, windows_error, selected_index, "window", "window",
                              visible_slots=ctx.config.sysmon_visible_slots))
    rows.append(("spacer", None))
    for line in _format_stats_grid(sysinfo_data, sensors_data, ctx.config.sysmon_blocks):
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
                stdscr.addstr(row, x + 2, wc_truncate(payload, max(inner_w, 0)), theme.get("accent", 0) | curses.A_BOLD)
            except curses.error:
                pass

        elif kind == "error":
            try:
                stdscr.addstr(row, x + 2, wc_truncate(f"⚠ {payload}", max(inner_w, 0)), theme.get("urgent", 0))
            except curses.error:
                pass

        elif kind == "empty_slot":
            try:
                stdscr.addstr(row, x + 2, wc_truncate(payload, max(inner_w, 0)), theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

        elif kind == "spacer":
            pass

        elif kind == "stats_line":
            # payload is a list of (text, role) segments from
            # _format_stats_grid — role in {"label","value","urgent"} —
            # drawn one after another so metric labels read in accent
            # color and values in plain text (see
            # CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid).
            # Clipped to inner_w across the whole row, not per-segment,
            # so a long last segment (HOT's own describe_sensor label)
            # still degrades the same way a plain string would have.
            col_x = x + 2
            remaining = max(inner_w, 0)
            role_colors = {
                "label": theme.get("accent", 0),
                "value": theme.get("text", 0),
                "warning": theme.get("warning", 0),
                "urgent": theme.get("urgent", 0),
            }
            try:
                for text, role in payload:
                    if remaining <= 0:
                        break
                    # Most segments here are internally-formatted ASCII
                    # numbers, but HOT's own describe_sensor() label is
                    # real hardware-reported text (lm-sensors) — always
                    # ASCII in practice, but not a guarantee worth
                    # baking in when the width-safe version costs
                    # nothing extra.
                    clipped = wc_truncate(text, remaining)
                    stdscr.addstr(row, col_x, clipped, role_colors.get(role, theme.get("text", 0)))
                    clipped_w = display_width(clipped)
                    col_x += clipped_w
                    remaining -= clipped_w
            except curses.error:
                pass

        elif kind == "diagnostics":
            # Same "●/○ dot signals status, independent of selection;
            # text color signals selection" split control.py's own
            # toggle rows already use — a plain text line didn't read
            # as a status the way every other module's dot-led rows do.
            diag = payload
            has_issues = _diagnostics_has_issues(diag)
            is_row_selected = "sysmon:diagnostics" == ctx.selected_id

            if diag is None or diag["summary"] == "Diagnostics unavailable":
                dot, dot_color = "○", theme.get("text", 0) | curses.A_DIM
            elif has_issues:
                dot, dot_color = "●", theme.get("urgent", 0)
            else:
                dot, dot_color = "●", theme.get("accent", 0)

            text_color = theme.get("selected", 0) if is_row_selected else theme.get("text", 0)
            attr = curses.A_BOLD if is_row_selected else 0
            summary = _diagnostics_summary_text(diag)
            # Discoverability for handle_diagnostics()'s copy action used
            # to be an inline "(Enter = copy to clipboard)" appended
            # right here — moved to its own boxed preview_footer (see
            # nav_items()'s diagnostics NavItem below and NavItem.
            # preview_footer's own docstring) for the same "controls
            # visually stand apart from information" reasoning
            # connectivity.py's own hints were moved there for.
            rest = wc_truncate(f" {summary}", max(inner_w - 1, 0))
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, rest, text_color | attr)
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
                stdscr.addstr(row, x + 2, wc_truncate(label, available_w), text_color | attr)
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
    windows = sort_windows_by_drain(windows)
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
            has_issues = _diagnostics_has_issues(diag)
            items.append(NavItem(
                id="sysmon:diagnostics",
                rect=(x + 1, row, w - 2, 1),
                focus_target=None,
                target_kind="sysmon_diagnostics",
                preview_text=_diagnostics_preview_text(diag, theme),
                preview_urgent=has_issues,
                # Discoverability for handle_diagnostics()'s copy action
                # — a separate boxed preview_footer (see NavItem.
                # preview_footer's own docstring, same pattern
                # connectivity.py's interaction hints use), not mixed
                # into preview_text itself: item.preview_text is what
                # handle_diagnostics() actually copies to the clipboard
                # verbatim, so the hint line living anywhere in THAT
                # list would get copied right along with the real
                # diagnostics breakdown. Only when there's something
                # worth copying (has_issues) — "All clear" needs no hint.
                preview_footer=(
                    [(f"[{key_label(ctx.config.keybinds['confirm'])}] Copy to clipboard", theme.get("urgent", 0))]
                    if has_issues else None
                ),
            ))

    # peek items for the scrollable window section, same mechanism
    # media.py's own nav_items() uses for Now Playing/Output.
    selected_index = _selected_window_index(windows, ctx.selected_id, _expanded_window_id)
    before_i, after_i = section_nav_indices(len(windows), selected_index,
                                             visible_slots=ctx.config.sysmon_visible_slots)
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


def handle_diagnostics(ctx, item, cfg):
    """Enter on the diagnostics summary row copies its own hover-preview
    breakdown (item.preview_text — the exact same lines shown in
    preview.py, not a re-fetch of raw diagnostics data) to the system
    clipboard, so a real journal/kernel error dump can be pasted into a
    search engine or bug report instead of only ever being readable
    inside tuicc's own narrow preview box. No confirm dialog, no
    dismiss — same "state action, stays visible" category as a toggle
    (VISION.md section 2), not a focus/power action.

    item.preview_text is None only when nothing's selected here at all
    (shouldn't happen — this handler only runs because the row WAS
    selected — but checked anyway rather than assumed, matching this
    codebase's own no-silent-failure discipline: a bug in that
    assumption should be a silent no-op, not a crash).

    Provider.copy_to_clipboard()'s own success/failure now surfaces as a
    real toast (ctx.toast_message/toast_urgent — see ActionContext's own
    docstring) instead of being silently swallowed — closes the gap this
    docstring used to flag as accepted-but-open.
    """
    if item.preview_text is None:
        return False, None
    text = "\n".join(line for line, _color in item.preview_text)
    if ctx.provider.copy_to_clipboard(text):
        ctx.toast_message = "Copied to clipboard"
    else:
        ctx.toast_message = "⚠ Copy failed — clipboard tool unavailable?"
        ctx.toast_urgent = True
    return False, None


HANDLERS = {
    "sysmon_row": handle_row,
    "sysmon_action": handle_action,
    "sysmon_diagnostics": handle_diagnostics,
}
