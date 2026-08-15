"""Tests for modules/sysmon.py's pure logic — expand/collapse state
machinery (same two-level model sessions.py/media.py established, see
test_sessions_module.py's own docstring for why module-level state is
reset per-test by hand), the NICE input quartet, row/nav_items
building, and handle_row/handle_action. draw() needs a real curses
screen, left untested here, same as every other module.
"""

from types import SimpleNamespace

import tuicc.modules.sysmon as sysmon_module
from tuicc.modules.sysmon import (
    GRID_COL_WIDTH, _build_rows, _diagnostics_has_issues,
    _diagnostics_summary_text, _format_stats_grid, _format_window_label,
    _friendly_app_name, _selected_window_index, _severity_role,
    _window_action_positions, apply_nice_edit, collapse, handle_action,
    handle_diagnostics, handle_row, handle_nice_key, is_editing_nice,
    is_expanded, nav_items, sort_windows_by_drain, start_nice_edit,
    visible_window_ids,
)
from tuicc.procmon import WindowStat


def _reset_module_state():
    sysmon_module._expanded_window_id = None
    sysmon_module._nice_target = None
    sysmon_module._nice_input = ""


def _win(window_id="1", app_id="firefox", title="Firefox", pid=100, cpu=10.0, rss_kb=2048):
    return WindowStat(window_id=window_id, app_id=app_id, title=title, pid=pid,
                       cpu_percent=cpu, rss_kb=rss_kb)


# ---------- is_expanded / collapse / _reconcile_expanded_state ----------

def test_is_expanded_reflects_expanded_window_id():
    _reset_module_state()
    assert is_expanded() is False
    sysmon_module._expanded_window_id = "1"
    assert is_expanded() is True


def test_collapse_clears_and_returns_the_window_id():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    assert collapse() == "1"
    assert is_expanded() is False


def test_collapse_when_nothing_expanded_returns_none():
    _reset_module_state()
    assert collapse() is None


def test_reconcile_clears_expanded_state_when_window_closes():
    _reset_module_state()
    sysmon_module._expanded_window_id = "gone"
    _build_rows(_ctx(windows=[_win(window_id="other")]), box_h=20)
    assert is_expanded() is False


def test_reconcile_leaves_expanded_state_when_window_still_present():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    _build_rows(_ctx(windows=[_win(window_id="1")]), box_h=20)
    assert is_expanded() is True


# ---------- NICE input quartet ----------

def test_start_nice_edit_opens_editing():
    _reset_module_state()
    assert is_editing_nice() is False
    start_nice_edit("1", pid=100, current=5)
    assert is_editing_nice() is True


def test_handle_nice_key_accepts_digits_only():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("1"))
    handle_nice_key(ord("0"))
    handle_nice_key(ord("x"))  # ignored, not a digit
    assert sysmon_module._nice_input == "10"


def test_handle_nice_key_backspace_removes_last_char():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("5"))
    handle_nice_key(127)
    assert sysmon_module._nice_input == ""


def test_handle_nice_key_escape_cancels_and_returns_false():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    still_claiming = handle_nice_key(27)
    assert still_claiming is False
    assert is_editing_nice() is False


def test_apply_nice_edit_success_calls_setpriority(monkeypatch):
    _reset_module_state()
    calls = []
    monkeypatch.setattr(sysmon_module.os, "setpriority", lambda which, pid, value: calls.append((which, pid, value)))
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("7"))
    result = apply_nice_edit()
    assert result == ("1", 7)
    assert calls == [(sysmon_module.os.PRIO_PROCESS, 100, 7)]
    assert is_editing_nice() is False


def test_apply_nice_edit_out_of_range_keeps_editing_open(monkeypatch):
    _reset_module_state()
    monkeypatch.setattr(sysmon_module.os, "setpriority", lambda *a: (_ for _ in ()).throw(AssertionError("should not be called")))
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("9"))
    handle_nice_key(ord("9"))  # "99" -> out of 0..19 range
    result = apply_nice_edit()
    assert result is None
    assert is_editing_nice() is True


def test_apply_nice_edit_empty_input_returns_none():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    assert apply_nice_edit() is None


def test_apply_nice_edit_when_nothing_editing_returns_none():
    _reset_module_state()
    assert apply_nice_edit() is None


# ---------- _selected_window_index / visible_window_ids ----------

def test_selected_window_index_from_row_selection():
    windows = [_win(window_id="a"), _win(window_id="b")]
    assert _selected_window_index(windows, "sysmon:b:row", expanded_window_id=None) == 1


def test_selected_window_index_from_expanded_state_takes_priority():
    windows = [_win(window_id="a"), _win(window_id="b")]
    assert _selected_window_index(windows, "sysmon:b:close", expanded_window_id="a") == 0


def test_visible_window_ids_fits_within_slots():
    _reset_module_state()
    windows = [_win(window_id=str(i)) for i in range(2)]
    assert visible_window_ids(windows, selected_id=None) == {"0", "1"}


def test_visible_window_ids_scrolls_to_keep_selection_visible():
    _reset_module_state()
    windows = [_win(window_id=str(i)) for i in range(6)]
    assert visible_window_ids(windows, selected_id="sysmon:4:row") == {"2", "3", "4"}


# ---------- sort_windows_by_drain ----------

def test_sort_windows_by_drain_most_cpu_first():
    windows = [
        _win(window_id="low", cpu=5.0, rss_kb=1000),
        _win(window_id="high", cpu=90.0, rss_kb=1000),
        _win(window_id="mid", cpu=40.0, rss_kb=1000),
    ]
    result = sort_windows_by_drain(windows)
    assert [w.window_id for w in result] == ["high", "mid", "low"]


def test_sort_windows_by_drain_rss_breaks_cpu_ties():
    windows = [
        _win(window_id="small", cpu=10.0, rss_kb=500),
        _win(window_id="big", cpu=10.0, rss_kb=5000),
    ]
    result = sort_windows_by_drain(windows)
    assert [w.window_id for w in result] == ["big", "small"]


def test_sort_windows_by_drain_unknown_cpu_sorts_last():
    windows = [
        _win(window_id="unknown", cpu=None, rss_kb=None),
        _win(window_id="known", cpu=1.0, rss_kb=1),
    ]
    result = sort_windows_by_drain(windows)
    assert [w.window_id for w in result] == ["known", "unknown"]


def test_sort_windows_by_drain_with_known_stats_lookup():
    # main.py's own use case: WindowInfo objects with no cpu/rss of
    # their own, sorted against the most recently known WindowStat
    # sample (matched by window_id) instead.
    window_infos = [
        SimpleNamespace(window_id="a"),
        SimpleNamespace(window_id="b"),
        SimpleNamespace(window_id="c"),  # no matching known stat at all
    ]
    known_stats = [
        _win(window_id="a", cpu=5.0, rss_kb=1),
        _win(window_id="b", cpu=90.0, rss_kb=1),
    ]
    result = sort_windows_by_drain(window_infos, known_stats=known_stats)
    assert [w.window_id for w in result] == ["b", "a", "c"]


# ---------- _window_action_positions ----------

def test_window_action_positions_right_aligned_and_ordered():
    positions = _window_action_positions(x=0, w=40)
    assert [action for action, _cx in positions] == ["close", "kill", "nice"]
    # Each position strictly increases left-to-right.
    xs = [cx for _action, cx in positions]
    assert xs == sorted(xs)


# ---------- _friendly_app_name / _format_window_label ----------

def test_friendly_app_name_matches_desktop_entry_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Visual Studio Code", "code", "Code"), ("Firefox", "firefox", "firefox")],
    )
    assert _friendly_app_name("code") == "Visual Studio Code"
    assert _friendly_app_name("firefox") == "Firefox"


def test_friendly_app_name_falls_back_to_raw_app_id_when_no_match(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    assert _friendly_app_name("some-custom-script") == "some-custom-script"


def test_friendly_app_name_none_or_empty_is_a_placeholder(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    assert _friendly_app_name(None) == "?"
    assert _friendly_app_name("") == "?"


def test_format_window_label_stats_always_shown_in_full(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    win = _win(app_id="firefox", cpu=13.0, rss_kb=24 * 1024)
    # Even with almost no room for a name, the "[13% 24M]" stats prefix
    # itself must never be the part that gets cut — a long window title
    # would otherwise push the CPU/RAM numbers off the edge entirely.
    label = _format_window_label(win, available_w=9)
    assert label.startswith("[13% 24M]")


def test_format_window_label_truncates_long_name_with_ellipsis(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Visual Studio Code", "code", "code")],
    )
    win = _win(app_id="code", cpu=10.0, rss_kb=1024)
    label = _format_window_label(win, available_w=20)
    assert label.startswith("[10% 1M] ")
    assert label.endswith("…")


def test_format_window_label_short_name_fits_untouched(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Firefox", "firefox", "firefox")],
    )
    win = _win(app_id="firefox", cpu=5.0, rss_kb=1024)
    label = _format_window_label(win, available_w=40)
    assert label == "[5% 1M] Firefox"


# ---------- _severity_role ----------

def test_severity_role_below_warning_is_value():
    assert _severity_role(50, warning=70, urgent=90) == "value"


def test_severity_role_at_warning_is_warning():
    assert _severity_role(70, warning=70, urgent=90) == "warning"


def test_severity_role_between_warning_and_urgent_is_warning():
    assert _severity_role(80, warning=70, urgent=90) == "warning"


def test_severity_role_at_or_above_urgent_is_urgent():
    assert _severity_role(90, warning=70, urgent=90) == "urgent"
    assert _severity_role(99, warning=70, urgent=90) == "urgent"


def test_severity_role_unknown_value_is_value_not_urgent():
    # None (poll hasn't completed, or a real error) must never read as
    # "bad" — unknown isn't "bad", it's just unknown.
    assert _severity_role(None, warning=70, urgent=90) == "value"


# ---------- _format_stats_grid ----------
# Each row is a list of (text, role) segments now, role in
# {"label","value","warning","urgent"} — draw() resolves role to a
# theme color (accent/text/warning/urgent respectively). _row_text()
# flattens a row back to a plain string for the tests that only care
# about CONTENT, not color. `blocks` is now ctx.config.sysmon_blocks'
# own shape — a list of [[sysmon.block]] dicts, not a flat thresholds
# dict — see config.py's DEFAULT_SYSMON_BLOCKS for the packaged layout
# _DEFAULT_BLOCKS (test_sysmon_module.py's own copy, top of file)
# reproduces.

def _row_text(row):
    return "".join(text for text, _role in row)


def _block(metric, column=1, row=1, warning=None, urgent=None, enabled=True, label=None):
    """A single [[sysmon.block]]-shaped dict — same keys config.py's
    own _build_sysmon_blocks() produces — for tests that only care
    about ONE metric's own behavior in isolation.
    """
    return {"metric": metric, "enabled": enabled, "column": column, "row": row,
            "warning": warning, "urgent": urgent, "label": label}


def test_format_stats_grid_shows_unknown_as_question_marks():
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=None, blocks=_DEFAULT_BLOCKS)
    assert "CPU" in _row_text(rows[0]) and "?%" in _row_text(rows[0])
    assert "RAM" in _row_text(rows[0])


def test_format_stats_grid_shows_real_values():
    # Grouped by column, not by row: CPU/CPUTEMP/HOT together (column
    # 1), RAM/DISK together (column 2), LOAD/SWAP together (column 3).
    # Rendered row-by-row, so row 0 is CPU+RAM+LOAD, row 1 is
    # CPUTEMP+DISK+SWAP, and row 2 is HOT alone (column 1's own 3rd
    # entry — the other two columns only have 2).
    sysinfo_data = {
        "cpu_percent": 23.4,
        "ram": {"total_kb": 16_000_000, "used_kb": 4_000_000, "available_kb": 12_000_000, "percent": 25.0},
        "disk": {"total": 500_000_000_000, "used": 100_000_000_000, "free": 400_000_000_000, "percent": 20.0},
        "load_average": (0.52, 0.58, 0.59),
        "throttled_recently": False, "swap_in_kb_s": 0.0, "swap_out_kb_s": 0.0,
    }
    sensors_data = {"cpu_temp": (58.0, "coretemp-isa-0000", "Package id 0"), "hottest": (58.0, "coretemp-isa-0000", "Package id 0")}
    rows = _format_stats_grid(sysinfo_data, sensors_data, _DEFAULT_BLOCKS)
    texts = [_row_text(r) for r in rows]
    assert len(rows) == 3
    assert "CPU" in texts[0] and "23%" in texts[0]
    # RAM: used/available in GiB (binary), not a bare percent.
    assert "RAM" in texts[0] and "3.8/11.4 GiB" in texts[0]
    assert "LOAD" in texts[0] and "0.5/0.6/0.6" in texts[0]
    assert "CPUTEMP" in texts[1] and "58°C" in texts[1]
    # DISK: used/free in GB (decimal), not a bare percent.
    assert "DISK" in texts[1] and "100/400 GB" in texts[1]
    assert "SWAP" in texts[1]
    # HOT gets the whole 3rd row to itself (nothing else has a 3rd
    # column entry) — this is the actual fix for it getting truncated
    # mid-word when it used to share a row with LOAD and CPUTEMP.
    assert "HOT" in texts[2] and "CPU (Package id 0)" in texts[2]


def test_format_stats_grid_throttled_flag_appends_to_hot_not_swap():
    # THROTTLED is a CPU-thermal flag — rides along on HOT's own cell
    # (the packaged default's own genuinely-unconstrained row), not
    # SWAP, which it has no real relation to.
    sysinfo_data = {
        "cpu_percent": None, "disk": None,
        "load_average": None, "throttled_recently": True,
        "swap_in_kb_s": None, "swap_out_kb_s": None,
    }
    rows = _format_stats_grid(sysinfo_data, None, _DEFAULT_BLOCKS)
    texts = [_row_text(r) for r in rows]
    assert any("HOT" in t and "THROTTLED" in t for t in texts)
    assert not any("SWAP" in t and "THROTTLED" in t for t in texts)


def test_format_stats_grid_throttled_segment_has_urgent_role():
    # THROTTLED should draw the eye once it's visible at all — its own
    # segment, colored "urgent" (draw() resolves this to
    # theme["urgent"]), not lumped into HOT's own plain-severity
    # temperature reading.
    sysinfo_data = {
        "cpu_percent": None, "disk": None,
        "load_average": None, "throttled_recently": True,
        "swap_in_kb_s": None, "swap_out_kb_s": None,
    }
    rows = _format_stats_grid(sysinfo_data, None, _DEFAULT_BLOCKS)
    hot_row = next(r for r in rows if "HOT" in _row_text(r))
    urgent_texts = [text for text, role in hot_row if role == "urgent"]
    assert any("THROTTLED" in t for t in urgent_texts)


def test_format_stats_grid_hot_row_is_not_truncated_by_column_width():
    # A long describe_sensor() label ("CPU (Package id 0)") must
    # survive in full on its own row — the whole point of grouping by
    # column instead of by row.
    sensors_data = {
        "cpu_temp": None,
        "hottest": (58.0, "coretemp-isa-0000", "Package id 0"),
    }
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=sensors_data, blocks=_DEFAULT_BLOCKS)
    assert _row_text(rows[2]) == "HOT  58°C (CPU (Package id 0))"


def test_format_stats_grid_columns_are_fixed_width():
    # The second column must always start at the same position
    # regardless of how long any individual value in the first column
    # is — that's the whole point of a real aligned grid.
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=None, blocks=_DEFAULT_BLOCKS)
    assert _row_text(rows[0])[GRID_COL_WIDTH:GRID_COL_WIDTH + 4] == "RAM "


def test_format_stats_grid_labels_use_label_role():
    # Metric labels (CPU, RAM, ...) render in accent color, values in
    # plain text color, so the block scans at a glance — draw()
    # resolves the "label" role to theme["accent"].
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=None, blocks=_DEFAULT_BLOCKS)
    label_texts = [text.strip() for text, role in rows[0] if role == "label"]
    assert "CPU" in label_texts
    assert "RAM" in label_texts


def test_format_stats_grid_values_use_value_role_not_label():
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=None, blocks=_DEFAULT_BLOCKS)
    value_texts = [text for text, role in rows[0] if role == "value"]
    assert any("?%" in t for t in value_texts)


def test_format_stats_grid_cpu_colored_by_threshold():
    sysinfo_data = {"cpu_percent": 95.0}
    rows = _format_stats_grid(sysinfo_data, None, [_block("cpu", warning=70, urgent=90)])
    cpu_cell = next((text, role) for text, role in rows[0] if "95%" in text)
    assert cpu_cell[1] == "urgent"  # 95 >= urgent (90)


def test_format_stats_grid_ram_colored_by_underlying_percent_not_displayed_amount():
    # RAM's own displayed text is used/available in GiB, not a percent
    # — but the COLOR still follows the real percent-full.
    sysinfo_data = {"ram": {"total_kb": 1000, "used_kb": 800, "available_kb": 200, "percent": 80.0}}
    rows = _format_stats_grid(sysinfo_data, None, [_block("ram", warning=75, urgent=90)])
    ram_cell = next((text, role) for text, role in rows[0] if "GiB" in text)
    assert ram_cell[1] == "warning"  # 80 is between warning (75) and urgent (90)


def test_format_stats_grid_disk_colored_by_percent():
    sysinfo_data = {"disk": {"total": 100, "used": 96, "free": 4, "percent": 96.0}}
    rows = _format_stats_grid(sysinfo_data, None, [_block("disk", warning=80, urgent=95)])
    disk_cell = next((text, role) for text, role in rows[0] if "GB" in text)
    assert disk_cell[1] == "urgent"  # 96 >= urgent (95)


def test_format_stats_grid_cputemp_and_hot_colored_by_temp_thresholds():
    sensors_data = {
        "cpu_temp": (95.0, "coretemp-isa-0000", "Package id 0"),
        "hottest": (78.0, "nvme-pci-3d00", "Composite"),
    }
    blocks = [_block("cputemp", column=1, row=1, warning=75, urgent=90),
              _block("hot", column=1, row=2, warning=75, urgent=90)]
    rows = _format_stats_grid(sysinfo_data=None, sensors_data=sensors_data, blocks=blocks)
    cputemp_cell = next((text, role) for text, role in rows[0] if "95°C" in text)
    assert cputemp_cell[1] == "urgent"  # 95 >= urgent (90)
    hot_cell = next((text, role) for text, role in rows[1] if "78°C" in text)
    assert hot_cell[1] == "warning"  # 78 is between warning (75) and urgent (90)


def test_format_stats_grid_block_without_thresholds_is_never_colored():
    # A block with no warning/urgent configured at all (LOAD/SWAP's own
    # packaged default) is deliberately never threshold-colored, even
    # given an extreme value — LOAD needs core-count normalization
    # (not implemented), SWAP has its own documented false-positive
    # risk (see CLAUDE/VISION.md's R6 section).
    sysinfo_data = {"load_average": (99.0, 99.0, 99.0)}
    rows = _format_stats_grid(sysinfo_data, None, [_block("load")])
    load_cell = next((text, role) for text, role in rows[0] if "99.0" in text)
    assert load_cell[1] == "value"


def test_format_stats_grid_disabled_block_is_skipped():
    # enabled = false hides a block entirely.
    sysinfo_data = {"cpu_percent": 50.0, "ram": {"total_kb": 1, "used_kb": 1, "available_kb": 0, "percent": 100.0}}
    blocks = [_block("cpu", column=1, row=1), _block("ram", column=1, row=2, enabled=False)]
    rows = _format_stats_grid(sysinfo_data, None, blocks)
    texts = [_row_text(r) for r in rows]
    assert not any("RAM" in t for t in texts)


def test_format_stats_grid_user_can_reposition_blocks_into_new_columns():
    # column/row are real config values, not fixed.
    sysinfo_data = {"cpu_percent": 10.0, "load_average": (1.0, 1.0, 1.0)}
    blocks = [_block("load", column=1, row=1), _block("cpu", column=2, row=1, warning=70, urgent=90)]
    rows = _format_stats_grid(sysinfo_data, None, blocks)
    assert len(rows) == 1
    # LOAD (column 1) now comes before CPU (column 2) — the opposite of
    # the packaged default's own column order.
    assert _row_text(rows[0]).index("LOAD") < _row_text(rows[0]).index("CPU")


def test_format_stats_grid_custom_label_overrides_metric_name():
    sysinfo_data = {"cpu_percent": 10.0}
    rows = _format_stats_grid(sysinfo_data, None, [_block("cpu", label="Processor")])
    assert "Processor" in _row_text(rows[0])
    assert "CPU " not in _row_text(rows[0])


# ---------- _diagnostics_summary_text ----------

def test_diagnostics_summary_text_none_shows_checking():
    # No "Diagnostics: " prefix anymore — the row's own ●/○ status dot
    # (draw()) carries that meaning now, same split control.py's own
    # dot-led toggle rows already use.
    assert _diagnostics_summary_text(None) == "checking..."


def test_diagnostics_summary_text_shows_summary():
    assert _diagnostics_summary_text({"summary": "All clear"}) == "All clear"


# ---------- _diagnostics_has_issues ----------

def test_diagnostics_has_issues_false_for_all_clear():
    assert _diagnostics_has_issues({"summary": "All clear"}) is False


def test_diagnostics_has_issues_false_when_unavailable():
    assert _diagnostics_has_issues({"summary": "Diagnostics unavailable"}) is False


def test_diagnostics_has_issues_false_when_not_polled_yet():
    assert _diagnostics_has_issues(None) is False


def test_diagnostics_has_issues_true_for_a_real_issue_count():
    assert _diagnostics_has_issues({"summary": "3 issues"}) is True


# ---------- _build_rows / nav_items ----------

class _FakeStatus:
    def __init__(self, snapshots=None, errors=None):
        self._snapshots = snapshots or {}
        self._errors = errors or {}

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def get_error(self, domain_name):
        return self._errors.get(domain_name)


# Same packaged layout as config.py's own DEFAULT_SYSMON_BLOCKS.
_DEFAULT_BLOCKS = [
    {"metric": "cpu", "enabled": True, "column": 1, "row": 1, "warning": 70, "urgent": 90, "label": None},
    {"metric": "cputemp", "enabled": True, "column": 1, "row": 2, "warning": 75, "urgent": 90, "label": None},
    {"metric": "hot", "enabled": True, "column": 1, "row": 3, "warning": 75, "urgent": 90, "label": None},
    {"metric": "ram", "enabled": True, "column": 2, "row": 1, "warning": 75, "urgent": 90, "label": None},
    {"metric": "disk", "enabled": True, "column": 2, "row": 2, "warning": 80, "urgent": 95, "label": None},
    {"metric": "load", "enabled": True, "column": 3, "row": 1, "warning": None, "urgent": None, "label": None},
    {"metric": "swap", "enabled": True, "column": 3, "row": 2, "warning": None, "urgent": None, "label": None},
]


def _ctx(windows=None, windows_error=None, sysinfo_data=None, sensors_data=None,
          diagnostics_data=None, selected_id=None):
    return SimpleNamespace(
        status=_FakeStatus(
            snapshots={"windows": windows, "sysinfo": sysinfo_data, "sensors": sensors_data,
                       "diagnostics": diagnostics_data},
            errors={"windows": windows_error},
        ),
        theme={}, selected_id=selected_id,
        config=SimpleNamespace(
            sysmon_blocks=_DEFAULT_BLOCKS, sysmon_visible_slots=3,
            keybinds={"confirm": ord("\n")},
        ),
        pending_confirm=None,
    )


def test_build_rows_header_shows_window_count():
    _reset_module_state()
    windows = [_win(window_id="1"), _win(window_id="2")]
    rows = _build_rows(_ctx(windows=windows), box_h=20)
    assert ("header", "Windows [2]") in rows


def test_build_rows_always_three_window_slots():
    _reset_module_state()
    for windows in ([], [_win(window_id="1")], [_win(window_id=str(i)) for i in range(6)]):
        rows = _build_rows(_ctx(windows=windows), box_h=20)
        section = [(k, p) for k, p in rows if k == "window" or (k == "empty_slot" and "window" in p)]
        assert len(section) == 3


def test_build_rows_respects_configured_visible_slots():
    # sysmon_visible_slots is a real config.toml value
    # (cfg.sysmon_visible_slots), not the hardcoded windowed_list.py
    # default.
    _reset_module_state()
    ctx = _ctx(windows=[_win(window_id=str(i)) for i in range(6)])
    ctx.config.sysmon_visible_slots = 5

    rows = _build_rows(ctx, box_h=20)

    section = [(k, p) for k, p in rows if k == "window" or (k == "empty_slot" and "window" in p)]
    assert len(section) == 5


def test_build_rows_includes_stats_lines_and_diagnostics():
    _reset_module_state()
    rows = _build_rows(_ctx(windows=[]), box_h=20)
    kinds = [k for k, _p in rows]
    assert kinds.count("stats_line") == 3
    assert "diagnostics" in kinds


def test_nav_items_collapsed_window_is_a_single_row_item():
    _reset_module_state()
    ctx = _ctx(windows=[_win(window_id="1")])
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    row_items = [it for it in items if it.target_kind == "sysmon_row"]
    assert [it.id for it in row_items] == ["sysmon:1:row"]


def test_nav_items_expanded_window_shows_three_actions():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    ctx = _ctx(windows=[_win(window_id="1")])
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    action_ids = [it.id for it in items if it.target_kind == "sysmon_action"]
    assert action_ids == ["sysmon:1:close", "sysmon:1:kill", "sysmon:1:nice"]


def test_nav_items_includes_diagnostics_row():
    _reset_module_state()
    ctx = _ctx(windows=[], diagnostics_data={"summary": "All clear", "failed_units": [], "oom_events": [], "general_errors": []})
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    diag_items = [it for it in items if it.target_kind == "sysmon_diagnostics"]
    assert len(diag_items) == 1
    assert diag_items[0].preview_text == [("No issues found", 0)]
    assert diag_items[0].preview_urgent is False
    # "All clear" has nothing worth copying — no footer hint at all,
    # same reasoning connectivity.py's own hints follow for an inert
    # state.
    assert diag_items[0].preview_footer is None


def test_nav_items_diagnostics_row_is_urgent_when_there_are_real_issues():
    _reset_module_state()
    ctx = _ctx(windows=[], diagnostics_data={
        "summary": "2 issues", "failed_units": ["foo.service"], "oom_events": [], "general_errors": [],
    })
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    diag_item = next(it for it in items if it.target_kind == "sysmon_diagnostics")
    assert diag_item.preview_urgent is True
    # The old inline "(Enter = copy to clipboard)" row-text hint moved
    # to its own boxed preview_footer (see NavItem.preview_footer's own
    # docstring, same pattern connectivity.py's interaction hints use)
    # — shown once there's actually something worth copying.
    assert diag_item.preview_footer == [("[Enter] Copy to clipboard", 0)]


# ---------- handle_row / handle_action ----------

class _FakeProvider:
    def __init__(self, copy_succeeds=True):
        self.closed = []
        self.clipboard = []
        self._copy_succeeds = copy_succeeds

    def close_window(self, window_id):
        self.closed.append(window_id)

    def copy_to_clipboard(self, text):
        self.clipboard.append(text)
        return self._copy_succeeds


class _FakeActionCtx:
    def __init__(self, windows=None, provider=None):
        self.provider = provider or _FakeProvider()
        self.status = _FakeStatus(snapshots={"windows": windows or []})
        self.reselect_item_id = None


def test_handle_row_expands_the_window():
    _reset_module_state()
    ctx = SimpleNamespace(reselect_item_id=None)
    item = SimpleNamespace(focus_target="1")

    should_dismiss, pending = handle_row(ctx, item, cfg=None)

    assert sysmon_module._expanded_window_id == "1"
    assert should_dismiss is False
    assert pending is None
    assert ctx.reselect_item_id == "sysmon:1:close"


def test_handle_action_close_calls_provider_and_collapses():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    provider = _FakeProvider()
    ctx = _FakeActionCtx(provider=provider)
    item = SimpleNamespace(focus_target="1:close")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert provider.closed == ["1"]
    assert is_expanded() is False
    assert should_dismiss is False
    assert pending is None


def test_handle_action_kill_returns_pending_confirm():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=4242)])
    item = SimpleNamespace(focus_target="1:kill")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending["command"] == "kill -9 4242"
    assert pending["shell_true"] is False
    assert pending["module"] == "sysmon"


def test_handle_action_kill_with_unknown_pid_is_a_noop():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=None)])
    item = SimpleNamespace(focus_target="1:kill")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending is None


def test_handle_action_nice_starts_editing():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=4242)])
    item = SimpleNamespace(focus_target="1:nice")

    handle_action(ctx, item, cfg=None)

    assert is_editing_nice() is True
    assert sysmon_module._nice_target["pid"] == 4242


# ---------- handle_diagnostics ----------

_DIAGNOSTICS_PREVIEW = [
    ("✗ unit.service failed", 1),
    ("kernel: some real error line", 2),
]


def test_handle_diagnostics_copies_preview_text_joined_by_newlines():
    provider = _FakeProvider(copy_succeeds=True)
    ctx = SimpleNamespace(provider=provider, toast_message=None, toast_urgent=False)
    item = SimpleNamespace(preview_text=_DIAGNOSTICS_PREVIEW)

    should_dismiss, pending = handle_diagnostics(ctx, item, cfg=None)

    assert provider.clipboard == ["✗ unit.service failed\nkernel: some real error line"]
    assert should_dismiss is False
    assert pending is None
    assert ctx.toast_message == "Copied to clipboard"
    assert ctx.toast_urgent is False


def test_handle_diagnostics_copy_failure_sets_urgent_toast():
    provider = _FakeProvider(copy_succeeds=False)
    ctx = SimpleNamespace(provider=provider, toast_message=None, toast_urgent=False)
    item = SimpleNamespace(preview_text=_DIAGNOSTICS_PREVIEW)

    should_dismiss, pending = handle_diagnostics(ctx, item, cfg=None)

    assert provider.clipboard == ["✗ unit.service failed\nkernel: some real error line"]
    assert should_dismiss is False
    assert ctx.toast_message == "⚠ Copy failed — clipboard tool unavailable?"
    assert ctx.toast_urgent is True


def test_handle_diagnostics_no_preview_text_is_a_noop():
    provider = _FakeProvider()
    ctx = SimpleNamespace(provider=provider, toast_message=None, toast_urgent=False)
    item = SimpleNamespace(preview_text=None)

    should_dismiss, pending = handle_diagnostics(ctx, item, cfg=None)

    assert provider.clipboard == []
    assert should_dismiss is False
    assert pending is None
    assert ctx.toast_message is None
